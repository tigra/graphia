# Graphia — Hosted AgentCore Deployment (spec 002)
#
# Root module for the AgentCore Runtime + Gateway + Memory + Observability
# stack that backs `uv run python -m graphia --remote`. Resources land in
# subsequent Phase 2 slices; this file currently declares only the provider
# wiring so that `terraform init` / `terraform plan` succeed against the
# skeleton.
#
# Authentication: the standard AWS credential chain is used (no profile
# literal in source). Set AWS_PROFILE in the shell or .env and run
# `aws sso login --profile <your-profile>` before invoking the wrapper.

provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}

# ---------------------------------------------------------------------------
# ECR repository — holds the Graphia Runtime container image. AgentCore
# Runtime pulls from this repository at boot.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "runtime" {
  name = "${local.name_prefix}-runtime"

  # Mutable tags so dev pushes can overwrite `:latest`. A production-grade
  # setup would flip this to IMMUTABLE and rely solely on git-SHA tags; for
  # the personal reference project that ADR 001 describes, MUTABLE is fine.
  image_tag_mutability = "MUTABLE"

  # Default off as a safeguard: `terraform destroy` will refuse to drop the
  # repo while it contains images, surfacing the destructive operation
  # rather than silently purging pushed layers. Override at destroy time
  # via `make tf-destroy ECR_FORCE_DELETE=true` when intentional teardown
  # is wanted (Slice 10's destroy verification, dev cycle resets, etc.).
  force_delete = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  # Default AWS-managed encryption (AES256) — no customer-managed KMS key.
  encryption_configuration {
    encryption_type = "AES256"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch log group — receives AgentCore Runtime traces / logs. The log
# group must exist before the Runtime resource is created so the Runtime's
# execution role can write to it. Slice 8 expands observability; the log
# group itself is created here.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "runtime" {
  name              = local.runtime_log_group
  retention_in_days = 30
}

# ---------------------------------------------------------------------------
# IAM execution role assumed by the AgentCore Runtime service. Trust policy
# allows the bedrock-agentcore service principal to assume the role; an
# inline policy grants the minimal permissions the Runtime needs at boot
# (ECR pull, Bedrock model invoke, CloudWatch log write).
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "runtime_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "runtime_inline" {
  # ECR — token-fetch must be unscoped (AWS requirement); image-pull actions
  # are scoped to the Graphia runtime repository ARN.
  statement {
    sid       = "EcrGetAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPullRuntimeImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.runtime.arn]
  }

  # Bedrock — InvokeModel and the streaming variant, scoped to the
  # foundation-model and US regional inference-profile ARNs Graphia uses.
  statement {
    sid    = "BedrockModelInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = local.bedrock_invoke_resources
  }

  # CloudWatch Logs — stream creation + log writes, scoped to the Runtime
  # log group's ARN.
  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.runtime.arn}:*"]
  }

  # AgentCore Memory — data-plane read/write actions scoped to the single
  # Memory resource provisioned below. The application calls
  # `MemoryClient.create_event` (Write) and `MemoryClient.list_events` (List)
  # against this Memory only. Action names verified against the AWS
  # service-authorization reference (see RESEARCH.md §8 for citations); the
  # spec brief's `bedrock-agentcore:BatchCreate/Update/Delete/Retrieve/...
  # MemoryRecords` action names are for the *records* surface that
  # `MemoryClient` does not use — granting them would be unused
  # least-privilege violations.
  statement {
    sid    = "AgentCoreMemoryReadWrite"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:ListEvents",
    ]
    resources = [aws_bedrockagentcore_memory.this.arn]
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${local.name_prefix}-runtime"
  assume_role_policy = data.aws_iam_policy_document.runtime_assume_role.json
}

resource "aws_iam_role_policy" "runtime" {
  name   = "${local.name_prefix}-runtime-inline"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime_inline.json
}

# ---------------------------------------------------------------------------
# AgentCore Memory — per-game diary store. Each diary entry is one Memory
# *event* keyed by (actor_id=player_id, session_id=game_id); raw events only,
# no extraction strategies (the application uses `MemoryClient.create_event`
# / `list_events` directly and parses the JSON payload itself — see
# `src/graphia/diary_store.py::AgentCoreMemoryDiaryStore`). Tags inherit
# from the provider's default_tags block. See RESEARCH.md §8 for the field
# rationale.
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_memory" "this" {
  name = local.memory_name

  # Days after which Memory events expire. Spec 002 doesn't specify; 90
  # days matches the Microgrid precedent (infrastructure-research.md §1.5)
  # and gives developers time to inspect post-game without manual cleanup.
  # Slice 10 (`terraform destroy`) drops the whole resource anyway, so the
  # expiry is primarily for in-life cleanup. Valid range per the provider:
  # 7–365.
  event_expiry_duration = 90

  description = "Graphia per-game diary store — one event per AI player's nightly diary entry, keyed by (player_id, game_id)."

  # No `memory_strategies` block: the application uses raw events end-to-end
  # (`create_event` / `list_events`), no semantic / summarisation / extraction
  # processing required. The provider models strategies as a separate
  # `aws_bedrockagentcore_memory_strategy` resource — we declare none.
}

# ---------------------------------------------------------------------------
# AgentCore Runtime — the hosted execution environment for the Graphia
# agent container. References the ECR image URI + the IAM execution role.
# Network mode is PUBLIC for v1.x; VPC mode is out of scope for spec 002.
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = local.runtime_name
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  # Plumb the Memory resource's ID into the container as `GRAPHIA_MEMORY_ID`.
  # `src/graphia/config.py::load_config()` reads this env var, and
  # `make_diary_store(config)` passes it to `MemoryClient.create_event(memory_id=...)`.
  # Referencing `aws_bedrockagentcore_memory.this.id` implicitly orders Memory
  # before Runtime — first-time deploys provision Memory first without an
  # explicit `depends_on`.
  environment_variables = {
    GRAPHIA_MEMORY_ID = aws_bedrockagentcore_memory.this.id
  }

  # Ensure the inline policy is in place before the Runtime is created;
  # `role_arn` only requires the role to exist, not its permissions, but
  # AgentCore fails on first model-invoke if the policy isn't attached yet.
  depends_on = [aws_iam_role_policy.runtime]
}
