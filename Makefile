# Auto-load .env so AWS_PROFILE (and other vars set there) flow into make and
# onward to ./tf, the aws CLI, and any spawned shells. The .env file is
# user-local and gitignored; create it via `make wire-env` after a deploy, or
# hand-edit it to set AWS_PROFILE before the first deploy.
ifneq (,$(wildcard .env))
include .env
export
endif

CONTAINER   ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || (command -v docker >/dev/null 2>&1 && echo docker || echo ""))
IMAGE       ?= graphia-runtime
TAG         ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)
PLATFORM    ?= linux/arm64
PORT        ?= 8080

# AWS_PROFILE drives auth. Configure it once (`aws configure sso` / `aws configure`)
# and set `AWS_PROFILE=<your-profile>` in `.env` — the account ID is derived from
# `aws sts get-caller-identity`, no separate AWS_ACCOUNT env var needed.
# Note: AWS_PROFILE is passed inline because Make's $(shell ...) runs in Make's
# invocation environment, not in Make's exported-variable scope.
AWS_ACCOUNT := $(shell AWS_PROFILE=$(AWS_PROFILE) aws sts get-caller-identity --query Account --output text 2>/dev/null)
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
# STATS_STRATEGY_ID: the self-managed career-stats strategy id from
# `make create-stats-strategy` (spec 006 / ADR 007). Empty until that runs;
# feed it back via `make tf-apply STATS_STRATEGY_ID=<id>` to plumb it to the
# Runtime as GRAPHIA_STATS_STRATEGY_ID. See infra/terraform/RESEARCH.md §14.
STATS_STRATEGY_ID ?=
TF_APPLY_VARS = $(TF_VARS) -var image_tag=$(TAG) -var stats_strategy_id=$(STATS_STRATEGY_ID)

LAMBDA_DIR    = infra/lambda
LAMBDA_BUILD  = $(LAMBDA_DIR)/.build
LAMBDA_FNS    = diary_write diary_read

# Lambda zips are produced by `make build-lambdas`. Each zip vendors the
# function's `requirements.txt` (bedrock-agentcore SDK) alongside
# `lambda_function.py`, ready for `aws_lambda_function.filename` to consume.
LAMBDA_ZIPS   = $(addprefix $(LAMBDA_BUILD)/,$(addsuffix .zip,$(LAMBDA_FNS)))

.PHONY: help check-container build run shell clean login-ecr push \
        tf-init tf-fmt tf-validate tf-plan tf-ecr-bootstrap tf-apply tf-destroy \
        wire-env deploy redeploy deploy-stats destroy inspect-diary play play-remote \
        build-lambdas clean-lambdas enable-transaction-search verify-observability \
        create-stats-strategy

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
	@echo "  make deploy             First-time: build-lambdas + tf-init + tf-ecr-bootstrap + push + tf-apply + wire-env, then deploy-stats (full Phase 3 bring-up)."
	@echo "  make redeploy           Steady-state code update: build-lambdas + push + tf-apply + wire-env (reads pinned STATS_STRATEGY_ID from .env)."
	@echo "  make deploy-stats       Convergent career-stats bring-up: tf-apply + create-stats-strategy + wire-env + tf-apply (pins + plumbs STATS_STRATEGY_ID)."
	@echo "  make wire-env           Discover the deployed Runtime URL + Memory id + log group (+ STATS_STRATEGY_ID, if a strategy exists) via the AWS API and write them into .env (no Terraform state needed)."
	@echo "  make destroy            Alias for tf-destroy."
	@echo ""
	@echo "Play:"
	@echo "  make play               uv run python -m graphia (local mode). Forward args: ARGS=\"--…\"."
	@echo "  make play-remote        uv run python -m graphia --remote (hits the deployed Runtime; uses .env)."
	@echo ""
	@echo "Inspection:"
	@echo "  make inspect-diary      Pretty-print diary entries from the deployed Memory (uses .env)."
	@echo ""
	@echo "Career stats (spec 006 / ADR 007 — self-managed long-term Memory strategy):"
	@echo "  make create-stats-strategy      Create the self-managed strategy out-of-band (provider gap); prints its strategyId."
	@echo ""
	@echo "Observability (one-time, per AWS account):"
	@echo "  make enable-transaction-search  Turn on CloudWatch Transaction Search for the trace tree."
	@echo ""
	@echo "Observability verification (live — drives the deployed Runtime):"
	@echo "  make verify-observability       Drive the deployed Runtime + inspect real CloudWatch telemetry."
	@echo ""
	@echo "Overrides:"
	@echo "  CONTAINER=docker|podman  IMAGE=...  TAG=...  PLATFORM=linux/amd64  PORT=..."
	@echo "  AWS_REGION=...  ENVIRONMENT=...  OWNER=...  ECR_REPO=...  REMOTE=..."

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
	@# Terraform evaluates filebase64sha256() on the Lambda zips even during destroy.
	@# Create empty placeholders with an epoch-0 mtime if the real zips aren't around,
	@# so destroy doesn't need a prior `make build-lambdas` AND a subsequent
	@# build-lambdas still triggers a real rebuild (sources stay newer than the
	@# placeholder).
	@mkdir -p $(LAMBDA_BUILD)
	@for f in $(LAMBDA_ZIPS); do \
	  if [ ! -e "$$f" ]; then \
	    touch "$$f"; \
	    touch -t 197001020000 "$$f"; \
	  fi; \
	done
	cd $(TF_DIR) && ./tf destroy $(TF_VARS)

# --- Workflow composites.

# Idempotent: replaces any existing GRAPHIA_RUNTIME_URL / GRAPHIA_MEMORY_ID /
# GRAPHIA_LOG_GROUP / OWNER / STATS_STRATEGY_ID lines in .env in place, preserves
# every other line. Creates .env if it doesn't exist.
#
# STATS_STRATEGY_ID is pinned ONLY when the deployed Memory already carries a
# self-managed (CUSTOM) strategy — i.e. after `make create-stats-strategy` has
# run. If none exists yet, .env is left untouched (never written empty/None), so
# a plain wire-env before the strategy is created can't blank the Runtime's
# GRAPHIA_STATS_STRATEGY_ID on the next apply. Once pinned, the include'd .env
# value wins over the STATS_STRATEGY_ID ?= default, just like OWNER.
#
# OWNER is resolved and pinned into .env first, before the deployment lookup:
#   1. the deployed ECR repo's `Owner` tag — the source of truth for who the
#      live stack is tagged to (so a fresh clone whose `git config user.email`
#      differs, can't drift the Owner tag on the next apply); failing that,
#   2. `git config user.email` (the same fallback the OWNER make-var uses), so a
#      fresh checkout with nothing deployed in AWS still gets OWNER populated.
# Because OWNER is written before the runtime existence check, it lands in .env
# even when no stack is deployed yet (wire-env then still errors on the missing
# runtime, but OWNER is already pinned).
#
# State-independent: instead of reading `./tf output` (which needs local
# Terraform state, so only works on the machine that deployed), it discovers
# the deployed resources via the AWS API by their conventional names. This
# means anyone with profile access can wire .env from a fresh clone. The
# names mirror infra/terraform/locals.tf: runtime/memory replace dashes with
# underscores (AgentCore name constraint); the log group keeps dashes.
# Requires a live SSO session for the active AWS profile.
wire-env:
	@set -e; \
	RUNTIME_NAME=$$(printf 'graphia-%s_runtime' "$(ENVIRONMENT)" | tr '-' '_' | cut -c1-48); \
	MEMORY_PREFIX=$$(printf 'graphia-%s_memory' "$(ENVIRONMENT)" | tr '-' '_' | cut -c1-48); \
	CAREER_MEMORY_PREFIX=$$(printf 'graphia-%s_career_memory' "$(ENVIRONMENT)" | tr '-' '_' | cut -c1-48); \
	LOG_GROUP="/aws/bedrock-agentcore/graphia-$(ENVIRONMENT)-runtime"; \
	touch .env; \
	ECR_ARN=$$(aws --region $(AWS_REGION) ecr describe-repositories \
	    --repository-names "graphia-$(ENVIRONMENT)-runtime" \
	    --query 'repositories[0].repositoryArn' --output text 2>/dev/null || true); \
	OWNER_TAG=""; \
	if [ -n "$$ECR_ARN" ] && [ "$$ECR_ARN" != "None" ]; then \
	  OWNER_TAG=$$(aws --region $(AWS_REGION) ecr list-tags-for-resource \
	      --resource-arn "$$ECR_ARN" \
	      --query "tags[?Key=='Owner'].Value | [0]" --output text 2>/dev/null || true); \
	fi; \
	if [ -z "$$OWNER_TAG" ] || [ "$$OWNER_TAG" = "None" ]; then \
	  OWNER_TAG=$$(git config user.email 2>/dev/null || echo unknown); \
	fi; \
	awk -v ow="OWNER=$$OWNER_TAG" \
	    'BEGIN { oseen=0 } \
	     /^OWNER=/ { print ow; oseen=1; next } \
	     { print } \
	     END { if (!oseen) print ow }' \
	    .env > .env.tmp && mv .env.tmp .env; \
	RUNTIME_URL=$$(aws --region $(AWS_REGION) bedrock-agentcore-control list-agent-runtimes \
	    --query "agentRuntimes[?agentRuntimeName=='$$RUNTIME_NAME'].agentRuntimeArn | [0]" --output text); \
	MEMORY_ID=$$(aws --region $(AWS_REGION) bedrock-agentcore-control list-memories \
	    --query "memories[?starts_with(id, '$$MEMORY_PREFIX')].id | [0]" --output text); \
	if [ -z "$$RUNTIME_URL" ] || [ "$$RUNTIME_URL" = "None" ]; then \
	  echo "OWNER pinned to '$$OWNER_TAG' in .env."; \
	  echo "ERROR: no AgentCore runtime named '$$RUNTIME_NAME' found in $(AWS_REGION)."; \
	  echo "       Is the stack deployed for ENVIRONMENT=$(ENVIRONMENT)? Run 'make deploy' (or set the right ENVIRONMENT)."; \
	  exit 1; \
	fi; \
	awk -v ru="GRAPHIA_RUNTIME_URL=$$RUNTIME_URL" \
	    -v mi="GRAPHIA_MEMORY_ID=$$MEMORY_ID" \
	    -v lg="GRAPHIA_LOG_GROUP=$$LOG_GROUP" \
	    'BEGIN { rseen=0; mseen=0; lseen=0 } \
	     /^GRAPHIA_RUNTIME_URL=/ { print ru; rseen=1; next } \
	     /^GRAPHIA_MEMORY_ID=/   { print mi; mseen=1; next } \
	     /^GRAPHIA_LOG_GROUP=/   { print lg; lseen=1; next } \
	     { print } \
	     END { if (!rseen) print ru; if (!mseen) print mi; if (!lseen) print lg }' \
	    .env > .env.tmp && mv .env.tmp .env; \
	CAREER_MEMORY_ID=$$(aws --region $(AWS_REGION) bedrock-agentcore-control list-memories \
	    --query "memories[?starts_with(id, '$$CAREER_MEMORY_PREFIX')].id | [0]" --output text); \
	if [ -n "$$CAREER_MEMORY_ID" ] && [ "$$CAREER_MEMORY_ID" != "None" ]; then \
	  STRATEGY_ID=$$(aws --region $(AWS_REGION) bedrock-agentcore-control get-memory \
	      --memory-id "$$CAREER_MEMORY_ID" \
	      --query "memory.strategies[?type=='CUSTOM']|[0].strategyId" --output text 2>/dev/null || true); \
	  if [ -n "$$STRATEGY_ID" ] && [ "$$STRATEGY_ID" != "None" ]; then \
	    awk -v si="STATS_STRATEGY_ID=$$STRATEGY_ID" \
	        'BEGIN { sseen=0 } \
	         /^STATS_STRATEGY_ID=/ { print si; sseen=1; next } \
	         { print } \
	         END { if (!sseen) print si }' \
	        .env > .env.tmp && mv .env.tmp .env; \
	  fi; \
	fi
	@echo ""
	@echo "Wired into .env (discovered via the AWS API — no Terraform state needed):"
	@grep -E '^(GRAPHIA_(RUNTIME_URL|MEMORY_ID|LOG_GROUP)|OWNER|STATS_STRATEGY_ID)=' .env

deploy: build-lambdas tf-init tf-ecr-bootstrap push tf-apply wire-env
	@echo ""
	@echo "Base stack up — running the Phase 3 career-stats bring-up..."
	$(MAKE) deploy-stats
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

# --- Phase 3 career-stats bring-up (spec 006 / ADR 007).
#
# The self-managed Memory strategy can't be a Terraform resource (provider gap,
# see create-stats-strategy), so first bring-up is a convergent four-step dance:
#   1. tf-apply             — creates the S3/SNS/IAM scaffolding + Memory exec role.
#   2. create-stats-strategy — CLI-creates the self-managed strategy out-of-band
#                              (idempotent: reuses an existing CUSTOM strategy).
#   3. wire-env             — discovers + pins STATS_STRATEGY_ID into .env.
#   4. tf-apply             — re-applies; reads the pinned id from .env so the
#                            Runtime gets GRAPHIA_STATS_STRATEGY_ID set.
# Fully idempotent / convergent: re-running reuses the strategy, re-pins the same
# id, and the final apply is a no-op once the Runtime env var already matches.
deploy-stats:
	$(MAKE) tf-apply
	$(MAKE) create-stats-strategy
	$(MAKE) wire-env
	$(MAKE) tf-apply
	@echo ""
	@echo "Career-stats bring-up complete: STATS_STRATEGY_ID pinned in .env and"
	@echo "plumbed to the Runtime as GRAPHIA_STATS_STRATEGY_ID."

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

destroy:
	@# ECR's force_delete attribute is read from SAVED STATE at destroy time —
	@# passing -var ecr_force_delete=true on the destroy line alone is not enough.
	@# Two-step: targeted apply (auto-approved, single resource) flips the saved
	@# state's force_delete to true, then the real destroy can purge images and
	@# repository together. Same shape as the README's "Destroy procedure".
	cd $(TF_DIR) && ./tf apply -target=aws_ecr_repository.runtime -var environment=$(ENVIRONMENT) -var owner=$(OWNER) -var ecr_force_delete=true
	@$(MAKE) tf-destroy ECR_FORCE_DELETE=true

# Play the game in local mode (default) or against the deployed Runtime.
# Both forward extra CLI args via $(ARGS) for flags like --seed.
#   make play
#   make play-remote
#   make play ARGS="--seed 42"
play:
	$(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )uv run python -m graphia $(ARGS)

play-remote:
	$(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )uv run python -m graphia --remote $(ARGS)

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

# --- Career-stats self-managed strategy (spec 006 / ADR 007, one-time per deploy).
#
# The self-managed (custom) long-term-Memory strategy that backs remote-mode
# career stats can NOT be a Terraform resource: provider 6.44.0/6.47.0
# `aws_bedrockagentcore_memory_strategy` exposes only the LLM-extraction
# `*_OVERRIDE` custom types, with no SELF_MANAGED type and no S3/SNS
# `invocation_configuration` (infra/terraform/RESEARCH.md §14). Terraform DOES
# manage the payload-delivery scaffolding the strategy requires (S3 bucket, SNS
# topic, memory execution role); this target attaches the strategy itself
# out-of-band, mirroring `enable-transaction-search` (§12 steps 2-3).
#
# State-independent: discovers the Terraform-created Memory / bucket / topic /
# role via the AWS API by their conventional names (same approach as wire-env),
# so it works from a fresh clone with profile access. Prints the returned
# strategyId — feed it back so it reaches the Runtime:
#   make tf-apply STATS_STRATEGY_ID=<printed-id>
# Idempotent-ish: if a self-managed strategy already exists on the Memory, it
# prints that one's id instead of creating a duplicate.
STATS_NAMESPACE ?= /career/human-career/
create-stats-strategy:
	@set -e; \
	CAREER_MEMORY_PREFIX=$$(printf 'graphia-%s_career_memory' "$(ENVIRONMENT)" | tr '-' '_' | cut -c1-48); \
	MEMORY_ID=$$(aws --region $(AWS_REGION) bedrock-agentcore-control list-memories \
	    --query "memories[?starts_with(id, '$$CAREER_MEMORY_PREFIX')].id | [0]" --output text); \
	if [ -z "$$MEMORY_ID" ] || [ "$$MEMORY_ID" = "None" ]; then \
	  echo "ERROR: no AgentCore career memory starting '$$CAREER_MEMORY_PREFIX' in $(AWS_REGION). Deploy first (make deploy)."; exit 1; \
	fi; \
	EXISTING=$$(aws --region $(AWS_REGION) bedrock-agentcore-control get-memory \
	    --memory-id "$$MEMORY_ID" \
	    --query "memory.strategies[?type=='CUSTOM']|[0].strategyId" --output text 2>/dev/null || echo None); \
	if [ -n "$$EXISTING" ] && [ "$$EXISTING" != "None" ]; then \
	  echo "Self-managed strategy already exists on career memory: $$EXISTING"; \
	  echo "Re-apply Terraform to plumb it: make tf-apply STATS_STRATEGY_ID=$$EXISTING"; \
	  exit 0; \
	fi; \
	BUCKET="graphia-$(ENVIRONMENT)-stats-payload-$(AWS_ACCOUNT)"; \
	TOPIC_ARN="arn:aws:sns:$(AWS_REGION):$(AWS_ACCOUNT):graphia-$(ENVIRONMENT)-stats-payload"; \
	STRATEGY_NAME=$$(printf 'graphia_%s_career' "$(ENVIRONMENT)" | tr '-' '_' | cut -c1-48); \
	STRATEGIES_JSON=$$(printf '{"addMemoryStrategies":[{"customMemoryStrategy":{"name":"%s","configuration":{"selfManagedConfiguration":{"invocationConfiguration":{"payloadDeliveryBucketName":"%s","topicArn":"%s"}}}}}]}' \
	    "$$STRATEGY_NAME" "$$BUCKET" "$$TOPIC_ARN"); \
	echo "Adding self-managed strategy to career memory $$MEMORY_ID ..."; \
	aws --region $(AWS_REGION) bedrock-agentcore-control update-memory \
	    --memory-id "$$MEMORY_ID" \
	    --memory-strategies "$$STRATEGIES_JSON" >/dev/null; \
	STRATEGY_ID=$$(aws --region $(AWS_REGION) bedrock-agentcore-control get-memory \
	    --memory-id "$$MEMORY_ID" \
	    --query "memory.strategies[?type=='CUSTOM']|[0].strategyId" --output text); \
	echo ""; \
	echo "Created self-managed career-stats strategy: $$STRATEGY_ID"; \
	echo "Now plumb it to the Runtime:"; \
	echo "  make tf-apply STATS_STRATEGY_ID=$$STRATEGY_ID"

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
