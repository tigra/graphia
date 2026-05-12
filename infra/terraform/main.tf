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

  # Ensure the inline policy is in place before the Runtime is created;
  # `role_arn` only requires the role to exist, not its permissions, but
  # AgentCore fails on first model-invoke if the policy isn't attached yet.
  depends_on = [aws_iam_role_policy.runtime]
}
