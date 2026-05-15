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
# CloudWatch log group — receives AgentCore Runtime application logs via the
# vended-log-delivery pipeline declared further below. The log group must
# exist before the Runtime resource is created so the Runtime's execution
# role can write to it. 30-day retention.
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

  # AgentCore Gateway — the agent inside the Runtime invokes its own diary
  # tools through the Gateway-MCP front door (per ADR 002). The data-plane
  # action `bedrock-agentcore:InvokeGateway` is documented in the AgentCore
  # IAM reference (see RESEARCH.md §9) and is scoped here to the single
  # Gateway resource this stack creates. Inbound auth on the Gateway is
  # `AWS_IAM`, so the Runtime's SigV4-signed call against this ARN is the
  # caller identity the Gateway authorizes.
  #
  # Note: the previous `bedrock-agentcore:CreateEvent` / `ListEvents` Memory
  # grant on this role was removed in ADR 005 — the agent inside the Runtime
  # no longer talks directly to Memory; it now goes Gateway → Lambda → Memory,
  # and the Lambda execution role (not the Runtime role) carries the Memory
  # write/list permissions. See RESEARCH.md §11.
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

  # Default protocol (HTTP) — the Runtime hosts only the Slice 4 agent
  # stream surface at `/invocations`. Diary tools live in Lambda functions
  # behind the Gateway (per ADR 005), not in this container.

  # Plumb the Memory ID and the Gateway identifier into the container.
  # - `GRAPHIA_MEMORY_ID`: `src/graphia/config.py::load_config()` reads it.
  #   The deployed Runtime never instantiates `AgentCoreMemoryDiaryStore`
  #   directly (the gateway-first factory branch takes precedence), but
  #   the var is still plumbed so ad-hoc local Memory inspection from the
  #   container works when Gateway is unset.
  # - `GRAPHIA_GATEWAY_ID`: the agent's diary call path goes through
  #   Gateway-MCP. `make_diary_store(config)` reads this and constructs a
  #   `GatewayMCPDiaryStore` pointing at the Gateway's MCP endpoint; the
  #   Gateway forwards calls to the Lambda diary handlers.
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
# AgentCore Observability — Runtime vended log delivery (Slice 8 sub-task 1).
#
# AgentCore Observability has NO dedicated argument on the
# `aws_bedrockagentcore_agent_runtime` resource (provider 6.44.0 exposes only
# region / description / environment_variables / authorizer_configuration /
# lifecycle_configuration / protocol_configuration / request_header_configuration
# / tags — none observability-related). The console "Log delivery" + "Tracing"
# widgets map to the CloudWatch Logs *vended log delivery* pipeline: a
# delivery source (one per log type) connected to a delivery destination via
# a delivery. The AWS SDK form is `put_delivery_source` / `put_delivery_destination`
# / `create_delivery` (see RESEARCH.md §12). The provider exposes all three as
# first-class resources, so the Runtime-scoped half of Observability IS
# Terraform-expressible — declared below.
#
# The account-level half (CloudWatch Transaction Search) is a provider gap —
# see RESEARCH.md §12 for the out-of-band CLI workaround.

# Delivery source: APPLICATION_LOGS — the Runtime's stdout/stderr application
# logs. `resource_arn` is the Runtime ARN; `log_type` is the only value
# AgentCore (a Bedrock service) accepts for the logs stream.
resource "aws_cloudwatch_log_delivery_source" "runtime_logs" {
  name         = "${local.name_prefix}-runtime-logs"
  log_type     = "APPLICATION_LOGS"
  resource_arn = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

# Delivery source: TRACES — the Runtime's OpenTelemetry spans. AgentCore
# Runtime auto-instruments the container; this source surfaces those spans
# to the trace pipeline.
resource "aws_cloudwatch_log_delivery_source" "runtime_traces" {
  name         = "${local.name_prefix}-runtime-traces"
  log_type     = "TRACES"
  resource_arn = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

# Delivery destination for logs — the 30-day-retention CloudWatch log group
# declared above. `delivery_destination_type` is computed as `CWL` from the
# log-group ARN.
resource "aws_cloudwatch_log_delivery_destination" "runtime_logs" {
  name = "${local.name_prefix}-runtime-logs"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.runtime.arn
  }
}

# Delivery destination for traces — X-Ray. Spans land in the account's
# `aws/spans` log group once CloudWatch Transaction Search is enabled at the
# account level (RESEARCH.md §12). `delivery_destination_type = "XRAY"` takes
# no `delivery_destination_configuration` block (provider-documented shape).
resource "aws_cloudwatch_log_delivery_destination" "runtime_traces" {
  name                      = "${local.name_prefix}-runtime-traces"
  delivery_destination_type = "XRAY"
}

# Delivery: connect the APPLICATION_LOGS source to the CloudWatch log group.
resource "aws_cloudwatch_log_delivery" "runtime_logs" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.runtime_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.runtime_logs.arn
}

# Delivery: connect the TRACES source to the X-Ray destination.
resource "aws_cloudwatch_log_delivery" "runtime_traces" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.runtime_traces.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.runtime_traces.arn
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
  # Outbound: per ADR 005, the Gateway's only targets are the two diary
  # Lambdas. The `bedrock-agentcore:InvokeAgentRuntime` statement that
  # pointed at the Runtime was removed when the Runtime stopped serving as
  # an MCP-server target. Same-account Lambda invocation needs only an
  # identity-based policy on the gateway role (per
  # gateway-prerequisites-permissions.html), so no resource-based
  # `aws_lambda_permission` is required.
  statement {
    sid    = "GatewayInvokeDiaryLambdas"
    effect = "Allow"
    actions = [
      "lambda:InvokeFunction",
    ]
    resources = [
      aws_lambda_function.diary_write.arn,
      aws_lambda_function.diary_read.arn,
    ]
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
# Diary Lambdas (ADR 005) — two zip-deployed Python functions, one per diary
# tool. The Gateway forwards MCP `tools/call` invocations to these functions,
# which delegate to AgentCore Memory via the `bedrock-agentcore` SDK. The zip
# files are produced by `make build-lambdas` (vendors the SDK alongside
# `lambda_function.py`); the build command is intentionally **out-of-band**
# from `terraform apply` so the container-less Terraform wrapper does not
# need pip available. `source_code_hash` ties the deployed package to the
# zip contents so a re-`apply` after a `make build-lambdas` rebuilds the
# function in-place.
#
# Per the AgentCore Lambda-target docs the function receives `event` shaped
# as the `inputSchema.properties` dict; tool name is in
# `context.client_context.custom['bedrockAgentCoreToolName']` (target-name
# prefixed with `___`). Each handler currently exposes one tool, so the
# delimiter-stripping pattern in the docs is left for the day a target
# exposes multiple tools.
# ---------------------------------------------------------------------------

# Shared execution role for both diary Lambdas. Trust principal is
# `lambda.amazonaws.com`; the inline policy grants the bedrock-agentcore
# Memory data-plane permissions the handlers need plus CloudWatch Logs
# write to the per-function log groups.
data "aws_iam_policy_document" "lambda_diary_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_diary_inline" {
  # AgentCore Memory — the handlers call `MemoryClient.create_event` (Write)
  # and `MemoryClient.list_events` (List). These are the same two actions
  # the Runtime role previously held; per ADR 005 they move here, scoped to
  # the same single Memory resource.
  statement {
    sid    = "AgentCoreMemoryReadWrite"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:ListEvents",
    ]
    resources = [aws_bedrockagentcore_memory.this.arn]
  }

  # CloudWatch Logs — stream creation + log writes, scoped to both diary
  # Lambda log groups (one per function).
  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.diary_write.arn}:*",
      "${aws_cloudwatch_log_group.diary_read.arn}:*",
    ]
  }
}

resource "aws_iam_role" "lambda_diary" {
  name               = "${local.name_prefix}-lambda-diary"
  assume_role_policy = data.aws_iam_policy_document.lambda_diary_assume_role.json
}

resource "aws_iam_role_policy" "lambda_diary" {
  name   = "${local.name_prefix}-lambda-diary-inline"
  role   = aws_iam_role.lambda_diary.id
  policy = data.aws_iam_policy_document.lambda_diary_inline.json
}

# Pre-create log groups (instead of letting Lambda auto-create them on
# first invoke) so retention is explicit and tags inherit from default_tags.
resource "aws_cloudwatch_log_group" "diary_write" {
  name              = "/aws/lambda/${local.name_prefix}-diary-write"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "diary_read" {
  name              = "/aws/lambda/${local.name_prefix}-diary-read"
  retention_in_days = 30
}

resource "aws_lambda_function" "diary_write" {
  function_name = "${local.name_prefix}-diary-write"
  role          = aws_iam_role.lambda_diary.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["x86_64"]
  timeout       = 30
  memory_size   = 256

  filename         = "${path.module}/../lambda/.build/diary_write.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda/.build/diary_write.zip")

  environment {
    variables = {
      GRAPHIA_MEMORY_ID = aws_bedrockagentcore_memory.this.id
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda_diary,
    aws_cloudwatch_log_group.diary_write,
  ]
}

resource "aws_lambda_function" "diary_read" {
  function_name = "${local.name_prefix}-diary-read"
  role          = aws_iam_role.lambda_diary.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["x86_64"]
  timeout       = 30
  memory_size   = 256

  filename         = "${path.module}/../lambda/.build/diary_read.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda/.build/diary_read.zip")

  environment {
    variables = {
      GRAPHIA_MEMORY_ID = aws_bedrockagentcore_memory.this.id
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda_diary,
    aws_cloudwatch_log_group.diary_read,
  ]
}

# ---------------------------------------------------------------------------
# Gateway targets — one per diary Lambda. The provider in 6.44.0 exposes the
# `target_configuration.mcp.lambda` block natively (verified against
# `internal/service/bedrockagentcore/gateway_target.go`: `tool_schema {
# inline_payload {...} }` with required `name`, `description`, `input_schema`).
# Same-account Lambda invocation uses `credential_provider_configuration {
# gateway_iam_role {} }`; the empty block is the documented Lambda shape
# (the `service` attribute the `mcp_server` shape needs is Lambda-irrelevant
# and not exposed by the provider). See RESEARCH.md §11.
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "diary_write" {
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  name               = "graphia-diary-write"
  description        = "Append one diary entry — Gateway-routed Lambda tool."

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.diary_write.arn

        tool_schema {
          inline_payload {
            name        = "diary_write"
            description = "Append one diary entry for (game_id, player_id) at the given night_index."

            input_schema {
              type = "object"

              property {
                name        = "game_id"
                type        = "string"
                description = "Game / thread identifier — scopes the entry to one game."
                required    = true
              }
              property {
                name        = "player_id"
                type        = "string"
                description = "Player identifier owning the diary entry."
                required    = true
              }
              property {
                name        = "night_index"
                type        = "integer"
                description = "Zero-based night index when the entry was written."
                required    = true
              }
              property {
                name        = "content"
                type        = "string"
                description = "The diary entry text — free-form private reasoning."
                required    = true
              }
            }

            output_schema {
              type = "object"

              property {
                name        = "ok"
                type        = "boolean"
                description = "Always true on success; failures surface as MCP isError=true."
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "diary_read" {
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  name               = "graphia-diary-read"
  description        = "List diary entries — Gateway-routed Lambda tool."

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.diary_read.arn

        tool_schema {
          inline_payload {
            name        = "diary_read"
            description = "List all diary entries for (game_id, player_id), sorted by night_index."

            input_schema {
              type = "object"

              property {
                name        = "game_id"
                type        = "string"
                description = "Game / thread identifier — scopes the result to one game."
                required    = true
              }
              property {
                name        = "player_id"
                type        = "string"
                description = "Player identifier whose entries to list."
                required    = true
              }
            }

            output_schema {
              type = "object"

              property {
                name        = "entries"
                type        = "array"
                description = "Diary entries sorted by night_index. Empty when the (game_id, player_id) pair has none."
                required    = true

                items {
                  type = "object"

                  property {
                    name        = "night_index"
                    type        = "integer"
                    description = "Zero-based night index when the entry was written."
                  }
                  property {
                    name        = "content"
                    type        = "string"
                    description = "The diary entry text."
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
