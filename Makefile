CONTAINER   ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || (command -v docker >/dev/null 2>&1 && echo docker || echo ""))
IMAGE       ?= graphia-runtime
TAG         ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)
PLATFORM    ?= linux/arm64
PORT        ?= 8080

AWS_ACCOUNT ?= 123456789012
AWS_REGION  ?= us-east-1
ENVIRONMENT ?= demo
OWNER       ?= $(shell git config user.email 2>/dev/null || echo unknown)
ECR_REPO    ?= graphia-$(ENVIRONMENT)-runtime
ECR_REGISTRY = $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com
REMOTE      ?= $(ECR_REGISTRY)/$(ECR_REPO)

# Off by default — `make tf-destroy` will refuse if ECR has images.
# Override with: make tf-destroy ECR_FORCE_DELETE=true
ECR_FORCE_DELETE ?= false

TF_DIR        = infra/terraform
TF_VARS       = -var environment=$(ENVIRONMENT) -var owner=$(OWNER) -var ecr_force_delete=$(ECR_FORCE_DELETE)
TF_APPLY_VARS = $(TF_VARS) -var image_tag=$(TAG)

.PHONY: help check-container build run shell clean login-ecr push \
        tf-init tf-fmt tf-validate tf-plan tf-ecr-bootstrap tf-apply tf-destroy \
        deploy redeploy destroy

help:
	@echo "Container image targets:"
	@echo "  make build              Build the runtime image ($(IMAGE):$(TAG)) for $(PLATFORM)."
	@echo "  make run                Build then run the container, mapping $(PORT):8080 locally."
	@echo "  make shell              Build then drop into /bin/sh inside the container."
	@echo "  make login-ecr          Authenticate the container runtime against ECR ($(ECR_REGISTRY))."
	@echo "  make push               Build, log into ECR, tag, and push :$(TAG) and :latest to $(REMOTE)."
	@echo "  make clean              Remove the local image tags."
	@echo ""
	@echo "Terraform-tool wrappers (run inside $(TF_DIR), via the ./tf container wrapper):"
	@echo "  make tf-init            Initialise the Terraform module."
	@echo "  make tf-fmt             Format the .tf files."
	@echo "  make tf-validate        Validate the configuration."
	@echo "  make tf-plan            Plan with current ENVIRONMENT/OWNER/TAG."
	@echo "  make tf-ecr-bootstrap   Apply only aws_ecr_repository.runtime (first-time bootstrap)."
	@echo "  make tf-apply           Full apply with image_tag=\$$TAG."
	@echo "  make tf-destroy         Destroy the whole stack."
	@echo ""
	@echo "Workflow composites:"
	@echo "  make deploy             First-time: tf-init + tf-ecr-bootstrap + push + tf-apply."
	@echo "  make redeploy           Steady-state code update: push + tf-apply."
	@echo "  make destroy            Alias for tf-destroy."
	@echo ""
	@echo "Overrides:"
	@echo "  CONTAINER=docker|podman  IMAGE=...  TAG=...  PLATFORM=linux/amd64  PORT=..."
	@echo "  AWS_ACCOUNT=...  AWS_REGION=...  ENVIRONMENT=...  OWNER=...  ECR_REPO=...  REMOTE=..."

check-container:
	@if [ -z "$(CONTAINER)" ]; then \
		echo "error: neither podman nor docker found on PATH. Install one of them and retry." >&2; \
		exit 1; \
	fi

build: check-container
	$(CONTAINER) build --platform $(PLATFORM) -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

run: build
	$(CONTAINER) run --rm -p $(PORT):8080 $(IMAGE):$(TAG)

shell: build
	$(CONTAINER) run --rm -it --entrypoint /bin/sh $(IMAGE):$(TAG)

login-ecr: check-container
	aws ecr get-login-password --region $(AWS_REGION) | $(CONTAINER) login --username AWS --password-stdin $(ECR_REGISTRY)

push: build login-ecr
	$(CONTAINER) tag $(IMAGE):$(TAG)    $(REMOTE):$(TAG)
	$(CONTAINER) tag $(IMAGE):latest    $(REMOTE):latest
	$(CONTAINER) push $(REMOTE):$(TAG)
	$(CONTAINER) push $(REMOTE):latest
	@echo ""
	@echo "Pushed $(REMOTE):$(TAG) and :latest"
	@echo "Now apply Terraform with the new tag:"
	@echo "  cd infra/terraform && ./tf apply -var environment=$(ENVIRONMENT) -var owner=<your-email> -var image_tag=$(TAG)"

clean: check-container
	-$(CONTAINER) rmi $(IMAGE):$(TAG) $(IMAGE):latest 2>/dev/null

# --- Terraform-tool wrappers (each shells into $(TF_DIR) and calls ./tf, which
# --- runs the pinned hashicorp/terraform image inside the container runtime.

tf-init:
	cd $(TF_DIR) && ./tf init

tf-fmt:
	cd $(TF_DIR) && ./tf fmt

tf-validate:
	cd $(TF_DIR) && ./tf validate

tf-plan:
	cd $(TF_DIR) && ./tf plan $(TF_APPLY_VARS)

tf-ecr-bootstrap:
	cd $(TF_DIR) && ./tf apply -target=aws_ecr_repository.runtime $(TF_VARS)

tf-apply:
	cd $(TF_DIR) && ./tf apply $(TF_APPLY_VARS)

tf-destroy:
	cd $(TF_DIR) && ./tf destroy $(TF_VARS)

# --- Workflow composites.

deploy: tf-init tf-ecr-bootstrap push tf-apply
	@echo ""
	@echo "Deploy complete. Runtime invocation URL:"
	@cd $(TF_DIR) && ./tf output runtime_invocation_url

redeploy: push tf-apply
	@echo ""
	@echo "Redeploy complete with image tag $(TAG)."

destroy: tf-destroy
