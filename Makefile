CONTAINER   ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || (command -v docker >/dev/null 2>&1 && echo docker || echo ""))
IMAGE       ?= graphia-runtime
TAG         ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)
PLATFORM    ?= linux/arm64
PORT        ?= 8080

AWS_ACCOUNT ?= 123456789012
AWS_REGION  ?= us-east-1
ENVIRONMENT ?= demo
ECR_REPO    ?= graphia-$(ENVIRONMENT)-runtime
ECR_REGISTRY = $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com
REMOTE      ?= $(ECR_REGISTRY)/$(ECR_REPO)

.PHONY: check-container build run shell clean help login-ecr push

help:
	@echo "Targets:"
	@echo "  make build              Build the runtime container image ($(IMAGE):$(TAG)) for $(PLATFORM)."
	@echo "  make run                Build then run the container, mapping $(PORT):8080 locally."
	@echo "  make shell              Build then drop into /bin/sh inside the container."
	@echo "  make login-ecr          Authenticate the container runtime against ECR ($(ECR_REGISTRY))."
	@echo "  make push               Build, log into ECR, tag, and push :$(TAG) and :latest to $(REMOTE)."
	@echo "  make clean              Remove the local image tags."
	@echo ""
	@echo "Overrides:"
	@echo "  CONTAINER=docker|podman  IMAGE=...  TAG=...  PLATFORM=linux/amd64  PORT=..."
	@echo "  AWS_ACCOUNT=...  AWS_REGION=...  ENVIRONMENT=...  ECR_REPO=...  REMOTE=..."

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
