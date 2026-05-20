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

LAMBDA_DIR    = infra/lambda
LAMBDA_BUILD  = $(LAMBDA_DIR)/.build
LAMBDA_FNS    = diary_write diary_read

# Lambda zips are produced by `make build-lambdas`. Each zip vendors the
# function's `requirements.txt` (bedrock-agentcore SDK) alongside
# `lambda_function.py`, ready for `aws_lambda_function.filename` to consume.
LAMBDA_ZIPS   = $(addprefix $(LAMBDA_BUILD)/,$(addsuffix .zip,$(LAMBDA_FNS)))

.PHONY: help check-container build run shell clean login-ecr push \
        tf-init tf-fmt tf-validate tf-plan tf-ecr-bootstrap tf-apply tf-destroy \
        wire-env deploy redeploy destroy inspect-diary play play-remote \
        build-lambdas clean-lambdas enable-transaction-search verify-observability

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
	@echo "Lambda zip-build (ADR 005 — Gateway → Lambda diary tools):"
	@echo "  make build-lambdas      Pip-install + zip each function under $(LAMBDA_BUILD)/."
	@echo "  make clean-lambdas      Remove $(LAMBDA_BUILD)/."
	@echo ""
	@echo "Workflow composites:"
	@echo "  make deploy             First-time: build-lambdas + tf-init + tf-ecr-bootstrap + push + tf-apply + wire-env."
	@echo "  make redeploy           Steady-state code update: build-lambdas + push + tf-apply + wire-env."
	@echo "  make wire-env           Pull GRAPHIA_RUNTIME_URL + GRAPHIA_MEMORY_ID + GRAPHIA_LOG_GROUP from tf outputs into .env."
	@echo "  make destroy            Alias for tf-destroy."
	@echo ""
	@echo "Play:"
	@echo "  make play               uv run python -m graphia (local mode). Forward args: ARGS=\"--…\"."
	@echo "  make play-remote        uv run python -m graphia --remote (hits the deployed Runtime; uses .env)."
	@echo ""
	@echo "Inspection:"
	@echo "  make inspect-diary      Pretty-print diary entries from the deployed Memory (uses .env)."
	@echo ""
	@echo "Observability (one-time, per AWS account):"
	@echo "  make enable-transaction-search  Turn on CloudWatch Transaction Search for the trace tree."
	@echo ""
	@echo "Observability verification (live — drives the deployed Runtime):"
	@echo "  make verify-observability       Drive the deployed Runtime + inspect real CloudWatch telemetry."
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

# Idempotent: replaces any existing GRAPHIA_RUNTIME_URL / GRAPHIA_MEMORY_ID /
# GRAPHIA_LOG_GROUP lines in .env in place, preserves every other line.
# Creates .env if it doesn't exist. All `./tf output -raw` calls run inside
# the container wrapper, so the SSO session has to be live.
wire-env:
	@set -e; \
	RUNTIME_URL=$$(cd $(TF_DIR) && ./tf output -raw runtime_invocation_url); \
	MEMORY_ID=$$(cd $(TF_DIR) && ./tf output -raw memory_id); \
	LOG_GROUP=$$(cd $(TF_DIR) && ./tf output -raw cloudwatch_log_group); \
	touch .env; \
	awk -v ru="GRAPHIA_RUNTIME_URL=$$RUNTIME_URL" \
	    -v mi="GRAPHIA_MEMORY_ID=$$MEMORY_ID" \
	    -v lg="GRAPHIA_LOG_GROUP=$$LOG_GROUP" \
	    'BEGIN { rseen=0; mseen=0; lseen=0 } \
	     /^GRAPHIA_RUNTIME_URL=/ { print ru; rseen=1; next } \
	     /^GRAPHIA_MEMORY_ID=/   { print mi; mseen=1; next } \
	     /^GRAPHIA_LOG_GROUP=/   { print lg; lseen=1; next } \
	     { print } \
	     END { if (!rseen) print ru; if (!mseen) print mi; if (!lseen) print lg }' \
	    .env > .env.tmp && mv .env.tmp .env
	@echo ""
	@echo "Wired into .env from Terraform outputs:"
	@grep -E '^GRAPHIA_(RUNTIME_URL|MEMORY_ID|LOG_GROUP)=' .env

deploy: build-lambdas tf-init tf-ecr-bootstrap push tf-apply wire-env
	@echo ""
	@echo "Deploy complete. Runtime invocation URL:"
	@cd $(TF_DIR) && ./tf output runtime_invocation_url
	@echo ""
	@echo "Next: launch a game against the deployed Runtime with:"
	@echo "  make play-remote"

redeploy: build-lambdas push tf-apply wire-env
	@echo ""
	@echo "Redeploy complete with image tag $(TAG)."
	@echo ""
	@echo "Next: launch a game against the deployed Runtime with:"
	@echo "  make play-remote"

# --- Lambda zip-build pipeline (ADR 005).
#
# Each function dir under $(LAMBDA_DIR)/ owns its own `requirements.txt`
# and `lambda_function.py`. The build step pip-installs the requirements
# into a per-function staging dir, copies the handler alongside, and zips
# the contents (NOT the parent dir) so the resulting archive layout matches
# what AWS Lambda's Python runtime expects: `lambda_function.py` and the
# vendored packages all at the zip root.
#
# Idempotent: each rule cleans its staging dir before re-installing, so
# stale package state doesn't leak between builds. Uses the host's `pip3`;
# this is a sub-second smoke-test build path, not a CI-grade reproducible
# build (no platform pinning, no `--python-version` switch). For the
# personal-reference project ADR 005 targets, the simple flow is fine.
build-lambdas: $(LAMBDA_ZIPS)
	@echo ""
	@echo "Built Lambda zips:"
	@ls -lh $(LAMBDA_ZIPS)

# Lambda runtime target. The functions are `runtime = "python3.13"` with
# the default x86_64 architecture, so deps must be Linux x86_64 wheels —
# NOT the host's wheels (a macOS / arm64 dev box would otherwise ship a
# `pydantic_core` .so that can't load on Lambda: `No module named
# 'pydantic_core._pydantic_core'`). `--platform` + `--only-binary=:all:`
# forces pip to download the manylinux wheels regardless of build host.
LAMBDA_PY_PLATFORM ?= manylinux2014_x86_64
LAMBDA_PY_VERSION  ?= 3.13

# Pattern rule: $(LAMBDA_BUILD)/<name>.zip depends on the function's
# lambda_function.py and requirements.txt; rebuilds whenever either changes.
$(LAMBDA_BUILD)/%.zip: $(LAMBDA_DIR)/%/lambda_function.py $(LAMBDA_DIR)/%/requirements.txt
	@mkdir -p $(LAMBDA_BUILD)
	rm -rf $(LAMBDA_BUILD)/$*
	mkdir -p $(LAMBDA_BUILD)/$*
	pip3 install --quiet -r $(LAMBDA_DIR)/$*/requirements.txt -t $(LAMBDA_BUILD)/$* \
		--platform $(LAMBDA_PY_PLATFORM) \
		--python-version $(LAMBDA_PY_VERSION) \
		--implementation cp \
		--only-binary=:all:
	cp $(LAMBDA_DIR)/$*/lambda_function.py $(LAMBDA_BUILD)/$*/
	cd $(LAMBDA_BUILD)/$* && zip -qr ../$*.zip .

clean-lambdas:
	rm -rf $(LAMBDA_BUILD)

destroy: tf-destroy

# Play the game in local mode (default) or against the deployed Runtime.
# Both forward extra CLI args via $(ARGS) for flags like --seed.
#   make play
#   make play-remote
#   make play ARGS="--seed 42"
play:
	uv run python -m graphia $(ARGS)

play-remote:
	uv run python -m graphia --remote $(ARGS)

# Pretty-print diary entries from the deployed Memory. Forwards extra
# CLI args (--game-id ..., --player-id ..., --json) via $(ARGS):
#   make inspect-diary
#   make inspect-diary ARGS="--game-id <thread> --json"
inspect-diary:
	uv run python -m graphia.tools.inspect_diary $(ARGS)

# --- CloudWatch Transaction Search (one-time, per AWS account).
#
# AgentCore Observability's trace-tree view depends on CloudWatch Transaction
# Search. Its enablement is three steps (infra/terraform/RESEARCH.md §12):
# step 1 (the X-Ray -> CloudWatch Logs resource policy) is Terraform-managed
# (aws_cloudwatch_log_resource_policy); steps 2-3 below have no resource in
# ANY hashicorp/aws release, so they live here as host-run AWS CLI calls.
#
# Indexing is set to 100%, not the high-volume default of 1%: Graphia is
# low-traffic, so 1% probabilistic indexing would leave most games unindexed
# and absent from the searchable Sessions view. At Graphia's span volume the
# cost of 100% indexing is negligible.
#
# Idempotent — safe to re-run. Uses the standard AWS credential chain
# (set AWS_PROFILE in the environment), same as `make login-ecr`.
enable-transaction-search:
	@if [ "$$(aws xray get-trace-segment-destination --region $(AWS_REGION) --query Destination --output text 2>/dev/null)" = "CloudWatchLogs" ]; then \
	    echo "Trace segment destination already CloudWatchLogs — skipping step 2."; \
	else \
	    aws xray update-trace-segment-destination --destination CloudWatchLogs --region $(AWS_REGION); \
	fi
	aws xray update-indexing-rule --name "Default" --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}' --region $(AWS_REGION)
	@echo ""
	@echo "CloudWatch Transaction Search enabled in $(AWS_REGION): trace segments -> CloudWatch Logs, 100% span indexing."

# --- Live observability verification.
#
# Drives the *deployed* Runtime with a partial scripted game, then polls
# CloudWatch (aws/spans + the runtime log group) and reports exactly what
# telemetry was recorded — the iteration loop for the trace-tree work.
# Opt-in: the test module skips unless GRAPHIA_LIVE_OBSERVABILITY_TEST=1, so
# the normal `uv run pytest -q` suite never touches AWS. Needs a deployed
# Runtime (GRAPHIA_RUNTIME_URL in .env) and a live SSO session.
verify-observability:
	GRAPHIA_LIVE_OBSERVABILITY_TEST=1 uv run pytest tests/test_remote_observability_live.py -s -v
