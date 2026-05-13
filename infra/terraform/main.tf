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

  # AgentCore Gateway — the agent inside the Runtime invokes its own diary
  # tools through the Gateway-MCP front door (per ADR 002). The data-plane
  # action `bedrock-agentcore:InvokeGateway` is documented in the AgentCore
  # IAM reference (see RESEARCH.md §9) and is scoped here to the single
  # Gateway resource this stack creates. Inbound auth on the Gateway is
  # `AWS_IAM`, so the Runtime's SigV4-signed call against this ARN is the
  # caller identity the Gateway authorizes.
  statement {
    sid       = "AgentCoreGatewayInvoke"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.this.gateway_arn]
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

  # Advertise MCP so the AgentCore Gateway target's synchronous tools/list
  # probe (run at gateway-target create time) talks to the Runtime's
  # FastMCP server at /mcp instead of attempting to invoke the agent
  # entrypoint as a generic Runtime. Required by Slice 7 sub-task 3's
  # Gateway-MCP refactor — without this block, CreateGatewayTarget fails
  # at terraform apply time.
  protocol_configuration {
    server_protocol = "MCP"
  }

  # Plumb the Memory ID and the Gateway identifier into the container.
  # - `GRAPHIA_MEMORY_ID`: `src/graphia/config.py::load_config()` reads it,
  #   and `make_diary_store(config)` passes it to
  #   `MemoryClient.create_event(memory_id=...)`.
  # - `GRAPHIA_GATEWAY_ID`: Slice 7 sub-task 3's refactor of the agent's diary
  #   call path through Gateway-MCP reads this to build the Gateway invocation
  #   URL (`https://bedrock-agentcore.${region}.amazonaws.com/...`) for the
  #   in-container MCP client. The `id` is the canonical handle the
  #   service-name lookup needs; the Gateway URL itself is also published as
  #   the `gateway_invocation_url` output so external local-mode flows can
  #   point at the same Gateway.
  # Implicit references to the Memory and Gateway resources establish the
  # correct ordering — no `depends_on` needed for those two specifically.
  environment_variables = {
    GRAPHIA_MEMORY_ID  = aws_bedrockagentcore_memory.this.id
    GRAPHIA_GATEWAY_ID = aws_bedrockagentcore_gateway.this.gateway_id
  }

  # Ensure the inline policy is in place before the Runtime is created;
  # `role_arn` only requires the role to exist, not its permissions, but
  # AgentCore fails on first model-invoke if the policy isn't attached yet.
  depends_on = [aws_iam_role_policy.runtime]
}

# ---------------------------------------------------------------------------
# AgentCore Gateway — single MCP front door for the agent's diary tools.
# Inbound auth is AWS_IAM so the Runtime (and any local-mode caller using
# the standard credential chain) can SigV4-sign requests against the Gateway
# ARN without the Cognito user-pool dependency that CUSTOM_JWT would pull
# in. Outbound auth on each target is the Gateway's own IAM role (SigV4
# against the Runtime's data-plane endpoint — see Gateway target blocks
# below). See RESEARCH.md §9 for the auth-model rationale.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "gateway_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "gateway_inline" {
  # Outbound: the Gateway invokes the Runtime's MCP endpoint via SigV4
  # using its execution role. The action is `bedrock-agentcore:InvokeAgentRuntime`
  # scoped to **all Runtime ARNs starting with `local.runtime_name`** — we
  # use the name-derived ARN pattern (not the resource attribute) to break a
  # Terraform dependency cycle: the Runtime references the Gateway (env var),
  # the Gateway role-policy references the Runtime (this statement). The
  # Runtime ARN shape is `arn:aws:bedrock-agentcore:${region}:${account}:runtime/${name}-<random>`
  # — see `runtime-mcp.html` example ARNs. The wildcard suffix matches the
  # service-generated random 10-char suffix that AgentCore appends to the
  # user-supplied name.
  statement {
    sid       = "InvokeAgentRuntime"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = ["arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:runtime/${local.runtime_name}-*"]
  }

  # CloudWatch Logs — the Gateway writes its own access / authorization
  # decision logs to a `/aws/bedrock-agentcore/${name}-gateway*` log group
  # (service-default destination, no explicit log-group resource needed at
  # this phase — Slice 8 sub-task 1 expands observability). We grant the
  # write surface preemptively, scoped to the Graphia AgentCore log prefix.
  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/*:*"]
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${local.name_prefix}-gateway"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume_role.json
}

resource "aws_iam_role_policy" "gateway" {
  name   = "${local.name_prefix}-gateway-inline"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway_inline.json
}

resource "aws_bedrockagentcore_gateway" "this" {
  name            = local.gateway_name
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"

  description = "Graphia diary-tool front door. Fronts the Runtime's two MCP tool endpoints (diary.write, diary.read) per ADR 002."

  # Ensure the Gateway's execution role has its inline policy attached
  # before the Gateway resource is created; the gateway control-plane
  # validates the role can SigV4-sign against the target Runtime at creation
  # time on some target types.
  depends_on = [aws_iam_role_policy.gateway]
}

# ---------------------------------------------------------------------------
# Gateway targets — one per diary tool, both pointing at the Runtime's MCP
# endpoint. The Runtime exposes its MCP server at
# `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${ARN}/invocations`;
# the Gateway target's `mcp_server.endpoint` field carries that URL. The
# Runtime's MCP server is expected to advertise the two diary tools (write,
# read) via `tools/list`; Gateway's implicit sync indexes them on target
# creation.
#
# NOTE on ADR 002 contract drift: the brief describes HTTP routes
# `/tools/diary/{write,read}` on the Runtime, but Gateway's `mcp_server`
# target type requires the Runtime to speak MCP at `/mcp` (path is fixed by
# the AgentCore runtime-MCP contract). Sub-task 3 must therefore switch the
# Runtime container from custom HTTP routes to an MCP server exposing
# `diary_write` and `diary_read` as MCP tools (FastMCP / `@mcp.tool()`).
# This is the cleanest target type — `open_api_schema` would force adding a
# real API Gateway in front, which contradicts ADR 002's "runtime-embedded"
# stance. See RESEARCH.md §9 for the analysis.
# ---------------------------------------------------------------------------

locals {
  # Runtime's MCP invocation URL. The ARN is URL-encoded per the data-plane
  # contract (`/runtimes/{URL_ENCODED_ARN}/invocations`). Terraform's
  # `urlencode()` does the `:` → `%3A` and `/` → `%2F` substitution the
  # AgentCore documentation example does manually.
  runtime_mcp_endpoint = "https://bedrock-agentcore.${var.region}.amazonaws.com/runtimes/${urlencode(aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn)}/invocations"
}

resource "aws_bedrockagentcore_gateway_target" "diary_write" {
  name               = "graphia-diary-write"
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id

  description = "Append one diary entry for the given (game_id, player_id, night_index). Request: {game_id, player_id, night_index>=0, content}. Response: {ok: true}."

  target_configuration {
    mcp {
      mcp_server {
        endpoint = local.runtime_mcp_endpoint
      }
    }
  }

  # Gateway uses its execution role (SigV4) to invoke the Runtime MCP
  # endpoint. This is the supported pairing for AgentCore-Runtime-hosted
  # MCP servers per the AgentCore Gateway target-type documentation.
  credential_provider_configuration {
    gateway_iam_role {}
  }
}

resource "aws_bedrockagentcore_gateway_target" "diary_read" {
  name               = "graphia-diary-read"
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id

  description = "Read all diary entries for the given (game_id, player_id). Request: {game_id, player_id}. Response: {entries: [{night_index, content}, ...]}."

  target_configuration {
    mcp {
      mcp_server {
        endpoint = local.runtime_mcp_endpoint
      }
    }
  }

  credential_provider_configuration {
    gateway_iam_role {}
  }
}
