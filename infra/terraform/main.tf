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

  # CloudWatch Logs + X-Ray + metrics — the AgentCore Runtime execution-role
  # observability permissions, taken from the AWS "IAM Permissions for
  # AgentCore Runtime" doc (devguide: runtime-permissions, "AgentCore Runtime
  # execution role"). The Runtime writes its container / service logs to the
  # platform-managed group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`,
  # which it creates on first run — so the role needs CreateLogGroup +
  # CreateLogStream + PutLogEvents on that path. The X-Ray grant is what lets
  # the in-Runtime ADOT exporter ship trace segments; without it the GenAI
  # Observability trace tree is empty.
  #
  # The prior single `CloudWatchLogsWrite` statement was the root-cause bug:
  # scoped only to the Terraform-made `aws_cloudwatch_log_group.runtime`
  # (`/aws/bedrock-agentcore/graphia-demo-runtime`), with no CreateLogGroup and
  # no X-Ray — so the platform could neither create the runtimes log group
  # (it never appeared, while every other runtime's did) nor export spans.
  # The runtime role does not need write access to the vended-delivery group:
  # that delivery is handled by the CloudWatch Logs vended-delivery pipeline,
  # not by this role.
  statement {
    sid    = "CloudWatchLogGroup"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
    ]
  }

  statement {
    sid       = "CloudWatchDescribeLogGroups"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
  }

  statement {
    sid    = "CloudWatchLogStreamWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
    ]
  }

  # X-Ray — the in-Runtime ADOT / OpenTelemetry exporter ships trace segments
  # to X-Ray; CloudWatch Transaction Search then surfaces them in `aws/spans`
  # and the GenAI Observability trace tree. AWS scopes these actions to "*".
  statement {
    sid    = "XRayTraceExport"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }

  # CloudWatch custom metrics — AgentCore emits operational metrics under the
  # `bedrock-agentcore` namespace; the condition keeps the grant scoped to it.
  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
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

  description = "Graphia Memory — per-game diary events (Phase 2). Career stats moved to a dedicated Memory in Phase 3 (ADR 008)."

  # No inline `memory_strategies` block (the provider has none). The diary tier
  # uses raw events end-to-end (`create_event` / `list_events`); the career tier
  # lives on `aws_bedrockagentcore_memory.career` (ADR 008) with its own
  # self-managed strategy and execution-role attachment.
}

# ---------------------------------------------------------------------------
# AgentCore Memory — career-stats store (spec 006 / ADR 008). Distinct from the
# diary Memory above: per ADR 008 career stats need their own AgentCore Memory
# so their lifecycle, retention, and the self-managed strategy that backs the
# long-term `CareerStats` record are isolated from per-game diary events. The
# Runtime authors per-action career events here; a consumer Lambda (later
# sub-task) consolidates them into the long-term record under the self-managed
# strategy's namespace.
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_memory" "career" {
  name = local.career_memory_name

  # Same 90-day event expiry as the diary tier — per-action career events are
  # transient inputs the consumer Lambda folds into the long-term record;
  # they don't need indefinite retention. The long-term `CareerStats` record
  # itself lives under the self-managed strategy (no per-event expiry).
  event_expiry_duration = 90

  description = "Graphia Memory — Phase 3 career stats long-term record + per-action career events under the self-managed strategy, per ADR 008."

  # Execution role the Memory service assumes for the self-managed (custom)
  # strategy's payload delivery — writing batched event payloads to the S3
  # bucket and publishing job notifications to the SNS topic (ADR 007 / spec
  # 006 §2.2). The provider documents `memory_execution_role_arn` as "Required
  # when using custom memory strategies with model processing"; the self-managed
  # strategy needs it for S3/SNS delivery even though our auto-extraction
  # trigger is never fired (we author records on demand). The strategy itself
  # is attached OUT-OF-BAND (`make create-stats-strategy`) — provider 6.44.0's
  # `aws_bedrockagentcore_memory_strategy` exposes only the four `*_OVERRIDE`
  # LLM-extraction types, no SELF_MANAGED / invocation_configuration surface.
  # See RESEARCH.md §14. Per ADR 008 the attachment moves from the diary
  # Memory to this career Memory.
  memory_execution_role_arn = aws_iam_role.memory_stats.arn

  # Wait for the memory-stats execution role to propagate through IAM before
  # AgentCore's UpdateMemory validates its trust policy (see the
  # `time_sleep.wait_memory_stats_role` rationale below). This adds ORDERING
  # only — `depends_on` does not force replacement, so existing Memory state is
  # untouched on a steady-state apply; the time_sleep persists.
  depends_on = [time_sleep.wait_memory_stats_role]
}

# ---------------------------------------------------------------------------
# Career-stats long-term-Memory payload-delivery scaffolding (spec 006 / ADR
# 007 — Layer 1). A self-managed (custom) Memory strategy REQUIRES an S3
# bucket + SNS topic + IAM role for its payload-delivery `invocationConfiguration`
# (AWS devguide: memory-self-managed-strategies), even though Graphia never
# fires the auto-extraction trigger — career records are authored on demand via
# the batch-record APIs. This is the accepted standing-infrastructure cost of a
# faithful long-term-Memory demonstration (ADR 007 §5).
#
# Provider gap: provider 6.44.0 (and latest 6.47.0) `aws_bedrockagentcore_memory_strategy`
# has NO `SELF_MANAGED` configuration type and no `invocation_configuration`
# (S3/SNS) attribute — its `CUSTOM` `configuration.type` only supports
# `SEMANTIC_OVERRIDE` / `SUMMARY_OVERRIDE` / `USER_PREFERENCE_OVERRIDE` /
# `EPISODIC_OVERRIDE`, all LLM-extraction strategies. So the bucket/topic/role
# below ARE Terraform-managed, but the strategy that consumes them is created
# out-of-band (`make create-stats-strategy`), mirroring the Transaction Search
# steps 2–3 precedent (RESEARCH.md §12). Full rationale: RESEARCH.md §14.
# ---------------------------------------------------------------------------

# S3 bucket — AgentCore delivers batched event payloads here when a trigger
# fires. force_destroy so `terraform destroy` cleans it up without a manual
# empty step (payloads are transient processing artefacts, not durable data).
resource "aws_s3_bucket" "stats_payload" {
  bucket        = local.stats_payload_bucket
  force_destroy = true

  tags = {
    Name = local.stats_payload_bucket
  }
}

# Block all public access — the bucket only ever receives service-internal
# payload writes from the Memory execution role.
resource "aws_s3_bucket_public_access_block" "stats_payload" {
  bucket = aws_s3_bucket.stats_payload.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Server-side encryption at rest (AWS-managed SSE-S3 / AES256) — matches the
# ECR repo's AES256 posture; no customer-managed KMS key for this reference
# project.
resource "aws_s3_bucket_server_side_encryption_configuration" "stats_payload" {
  bucket = aws_s3_bucket.stats_payload.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle policy — auto-delete delivered payloads after 1 day. The AWS
# self-managed-strategy guide explicitly recommends this to control cost since
# payloads are consumed-then-discarded (we never even consume them: the trigger
# is unused). 1 day is the minimum meaningful expiry.
resource "aws_s3_bucket_lifecycle_configuration" "stats_payload" {
  bucket = aws_s3_bucket.stats_payload.id

  rule {
    id     = "expire-delivered-payloads"
    status = "Enabled"

    filter {}

    expiration {
      days = 1
    }
  }
}

# SNS topic — AgentCore publishes a job notification here when a trigger fires.
# Standard (non-FIFO) topic: career records are written on demand, not via the
# unused auto-extraction trigger, so per-session ordering is irrelevant.
resource "aws_sns_topic" "stats_payload" {
  name = local.stats_topic_name

  tags = {
    Name = local.stats_topic_name
  }
}

# IAM role the Memory service assumes for payload delivery. Trust principal is
# `bedrock-agentcore.amazonaws.com` (AWS self-managed-strategy trust policy);
# the inline policy is least-privilege — exactly the S3 + SNS actions the AWS
# guide lists, scoped to this bucket and this topic.
data "aws_iam_policy_document" "memory_stats_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "memory_stats_inline" {
  # S3 payload delivery — the two actions the AWS self-managed-strategy guide
  # grants: GetBucketLocation on the bucket, PutObject on its objects.
  statement {
    sid    = "S3PayloadDelivery"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:PutObject",
    ]
    resources = [
      aws_s3_bucket.stats_payload.arn,
      "${aws_s3_bucket.stats_payload.arn}/*",
    ]
  }

  # SNS notifications — GetTopicAttributes + Publish, scoped to the one topic.
  statement {
    sid    = "SNSNotifications"
    effect = "Allow"
    actions = [
      "sns:GetTopicAttributes",
      "sns:Publish",
    ]
    resources = [aws_sns_topic.stats_payload.arn]
  }
}

resource "aws_iam_role" "memory_stats" {
  name               = "${local.name_prefix}-memory-stats"
  assume_role_policy = data.aws_iam_policy_document.memory_stats_assume_role.json
}

resource "aws_iam_role_policy" "memory_stats" {
  name   = "${local.name_prefix}-memory-stats-inline"
  role   = aws_iam_role.memory_stats.id
  policy = data.aws_iam_policy_document.memory_stats_inline.json
}

# IAM-propagation delay for the memory-stats execution role. On a from-scratch
# apply, AgentCore's UpdateMemory validates the brand-new role's trust policy
# ~1s after the role is created — often before IAM has propagated globally —
# failing with `ValidationException: Please provide a role with a valid trust
# policy`. A retry succeeds (the role has propagated by then), but we want the
# first apply to work. This canonical fix forces a 30s pause between the role
# (+ its inline policy) being created and the Memory update that references it.
#
# No `triggers`: the sleep is keyed to the role's lifetime, not every apply.
# time_sleep creates ONCE and persists in state; steady-state applies do not
# re-sleep. It only re-runs if the role/policy it depends on is replaced.
resource "time_sleep" "wait_memory_stats_role" {
  create_duration = "30s"

  depends_on = [
    aws_iam_role.memory_stats,
    aws_iam_role_policy.memory_stats,
  ]
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

  # Plumb the Memory IDs and the Gateway identifier into the container.
  # - `GRAPHIA_MEMORY_ID`: `src/graphia/config.py::load_config()` reads it.
  #   The deployed Runtime never instantiates `AgentCoreMemoryDiaryStore`
  #   directly (the gateway-first factory branch takes precedence), but
  #   the var is still plumbed so ad-hoc local Memory inspection from the
  #   container works when Gateway is unset.
  # - `GRAPHIA_CAREER_MEMORY_ID`: the dedicated career-stats AgentCore Memory
  #   (ADR 008). The Runtime's career-stats store writes per-action events
  #   here, and the consumer Lambda (later sub-task) consolidates them into
  #   the long-term `CareerStats` record under the self-managed strategy's
  #   namespace.
  # - `GRAPHIA_GATEWAY_ID`: the agent's diary call path goes through
  #   Gateway-MCP. `make_diary_store(config)` reads this and constructs a
  #   `GatewayMCPDiaryStore` pointing at the Gateway's MCP endpoint; the
  #   Gateway forwards calls to the Lambda diary handlers.
  # - `GRAPHIA_STATS_STRATEGY_ID` / `GRAPHIA_STATS_NAMESPACE`: the remote-mode
  #   career-stats store (`AgentCoreLongTermStatsStore`, spec 006 §2.1) reads
  #   these to write/read the rolling career aggregate as a self-managed
  #   long-term Memory record on the career Memory. The strategy id comes from
  #   the `stats_strategy_id` var (the out-of-band-created strategy —
  #   RESEARCH.md §14); it is "" until the strategy is created, after which
  #   the store can also resolve it by listing the career Memory's strategies.
  #   The namespace is the constant career namespace local (matches the app's
  #   GRAPHIA_STATS_NAMESPACE default `/career/human-career/`).
  # Implicit references to the Memory and Gateway resources establish the
  # correct ordering — no `depends_on` needed for those two specifically.
  environment_variables = {
    GRAPHIA_MEMORY_ID         = aws_bedrockagentcore_memory.this.id
    GRAPHIA_CAREER_MEMORY_ID  = aws_bedrockagentcore_memory.career.id
    GRAPHIA_GATEWAY_ID        = aws_bedrockagentcore_gateway.this.gateway_id
    GRAPHIA_STATS_STRATEGY_ID = var.stats_strategy_id
    GRAPHIA_STATS_NAMESPACE   = local.stats_namespace
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
# CloudWatch Transaction Search — step 1 of 3 (RESEARCH.md §12).
#
# CloudWatch Transaction Search makes Runtime spans searchable in the GenAI
# Observability console. It is account-wide and split across three actions
# (RESEARCH.md §12): only the first — a CloudWatch Logs *resource* policy
# letting `xray.amazonaws.com` write spans into the AWS-managed Transaction
# Search log groups — has a provider surface. The CLI form is
# `aws logs put-resource-policy`, which maps to
# `aws_cloudwatch_log_resource_policy` (a CloudWatch Logs resource policy —
# NOT `aws_xray_resource_policy`, which is a different X-Ray-side resource).
#
# Steps 2 (`xray update-trace-segment-destination`) and 3
# (`xray update-indexing-rule`) have no provider resource in 6.44.0 and stay
# as the host-run CLI workaround documented in §12.
#
# The two target log groups (`aws/spans`, `/aws/application-signals/data`)
# are created by AWS when Transaction Search is enabled — not by this module —
# so they are referenced by constructed ARN string, never as
# `aws_cloudwatch_log_group` resources here. Account id comes from
# `data.aws_caller_identity.current` and region from `var.region`; neither is
# hardcoded. This is a region/account-scoped resource policy with no stored
# data, so a fresh `terraform apply` recreates it cleanly (Slice 10 destroy/
# redeploy test).
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "transaction_search" {
  statement {
    sid     = "TransactionSearchXRayAccess"
    effect  = "Allow"
    actions = ["logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:aws/spans:*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/application-signals/data:*",
    ]

    principals {
      type        = "Service"
      identifiers = ["xray.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:xray:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "transaction_search" {
  policy_name     = "${local.name_prefix}-transaction-search"
  policy_document = data.aws_iam_policy_document.transaction_search.json
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
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*:*"]
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

# ---------------------------------------------------------------------------
# Career-stats consumer Lambda (spec 006 / ADR 008 / Slice 8.8) — SNS-triggered
# Python function that subscribes to `aws_sns_topic.stats_payload`, downloads
# the self-managed strategy's batched event payload from
# `aws_s3_bucket.stats_payload`, decodes the inner `CareerEvent` JSON, and on
# finalizer events (game_ended / game_abandoned) folds the full session into
# the long-term `CareerStats` record on the career Memory.
#
# Pattern mirrors the two diary Lambdas above: zip built out-of-band by
# `make build-lambdas`, runtime/handler/timeout/memory identical, pre-created
# log group with 30-day retention, dedicated inline-policy execution role
# (NOT shared with `aws_iam_role.memory_stats` — that one is the *strategy's*
# delivery role with `bedrock-agentcore` as the trust principal; this is the
# *Lambda's* execution role with `lambda.amazonaws.com` as the trust principal,
# least-privilege scoped to exactly what the handler reads/writes).
# ---------------------------------------------------------------------------

# Dedicated execution role for the career-consumer Lambda. Trust principal is
# `lambda.amazonaws.com`; the inline policy grants S3 read on the payload
# bucket, the bedrock-agentcore Memory data-plane actions the handler issues
# (ListEvents to page the session, ListMemoryRecords to find the existing
# career record, BatchCreate/UpdateMemoryRecords to persist the fold), and
# CloudWatch Logs write to its own log group.
data "aws_iam_policy_document" "lambda_career_consumer_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_career_consumer_inline" {
  # S3 — download the self-managed strategy's batched payloads. The handler
  # calls `s3:GetObject` against `s3PayloadLocation` URIs published by the
  # Memory service to the SNS topic, which always point inside this bucket.
  statement {
    sid       = "S3PayloadRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.stats_payload.arn}/*"]
  }

  # AgentCore Memory — scoped to the **career** Memory ARN (distinct from the
  # diary Memory the diary Lambdas reference). ListEvents pages the session,
  # ListMemoryRecords locates the existing long-term record (if any), and the
  # two BatchCreate/UpdateMemoryRecords actions persist the folded record.
  statement {
    sid    = "AgentCoreCareerMemory"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:ListMemoryRecords",
      "bedrock-agentcore:BatchCreateMemoryRecords",
      "bedrock-agentcore:BatchUpdateMemoryRecords",
    ]
    resources = [aws_bedrockagentcore_memory.career.arn]
  }

  # CloudWatch Logs — stream creation + log writes on this function's own
  # pre-created log group only (mirrors the diary Lambda pattern: the log
  # group is declared as an `aws_cloudwatch_log_group` resource with 30-day
  # retention, and the policy references it by ARN).
  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.career_consumer.arn}:*"]
  }
}

resource "aws_iam_role" "career_consumer" {
  name               = "${local.name_prefix}-lambda-career-consumer"
  assume_role_policy = data.aws_iam_policy_document.lambda_career_consumer_assume_role.json
}

resource "aws_iam_role_policy" "career_consumer" {
  name   = "${local.name_prefix}-lambda-career-consumer-inline"
  role   = aws_iam_role.career_consumer.id
  policy = data.aws_iam_policy_document.lambda_career_consumer_inline.json
}

# Pre-create log group (instead of letting Lambda auto-create it on first
# invoke) so retention is explicit and tags inherit from default_tags —
# same approach as the diary Lambdas above.
resource "aws_cloudwatch_log_group" "career_consumer" {
  name              = "/aws/lambda/${local.name_prefix}-career-consumer"
  retention_in_days = 30
}

resource "aws_lambda_function" "career_consumer" {
  function_name = "${local.name_prefix}-career-consumer"
  role          = aws_iam_role.career_consumer.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.13"
  architectures = ["x86_64"]
  timeout       = 30
  memory_size   = 256

  filename         = "${path.module}/../lambda/.build/career_consumer.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda/.build/career_consumer.zip")

  environment {
    variables = {
      # Career Memory id — the handler reads/writes the long-term CareerStats
      # record on this Memory and pages session events from it.
      CAREER_MEMORY_ID = aws_bedrockagentcore_memory.career.id

      # Career-stats namespace — single source of truth, shared with the
      # Runtime env (`GRAPHIA_STATS_NAMESPACE`) so the consumer Lambda and the
      # Runtime's remote-mode store target the same logical career bucket.
      STATS_NAMESPACE = local.stats_namespace

      # AWS_REGION is auto-populated by the Lambda service — boto3 clients in
      # the handler read it from the environment with no Terraform plumbing.
    }
  }

  depends_on = [
    aws_iam_role_policy.career_consumer,
    aws_cloudwatch_log_group.career_consumer,
  ]
}

# SNS subscription — wire the stats payload topic to the consumer Lambda. The
# topic has no other subscribers; this is its single fan-out.
resource "aws_sns_topic_subscription" "career_consumer" {
  topic_arn = aws_sns_topic.stats_payload.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.career_consumer.arn
}

# Resource-based permission — let SNS invoke the Lambda. Scoped to the one
# topic via `source_arn` so no other SNS topic in the account can trigger
# this function.
resource "aws_lambda_permission" "career_consumer_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.career_consumer.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.stats_payload.arn
}
