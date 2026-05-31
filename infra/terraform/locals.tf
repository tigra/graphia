locals {
  # Required tag set applied to every taggable resource via the provider's
  # default_tags block. Project and ManagedBy are constants for this module;
  # Environment and Owner come from input variables (see variables.tf).
  common_tags = {
    Project     = "Graphia"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = var.owner
  }

  # Naming-convention helper for all AgentCore resources in this module.
  # Capped at 80 chars to leave headroom for the AgentCore service-side
  # 10-char auto-suffix and any per-resource role suffix appended downstream
  # (see RESEARCH.md §6 Q4). All four AgentCore resources should derive their
  # names from this single prefix to avoid IAM-ARN-mismatch errors when names
  # drift between resources.
  name_prefix = substr("graphia-${var.environment}", 0, 80)

  # AgentCore Runtime names are stricter than ECR / IAM resource names:
  # the control-plane CreateAgentRuntime API requires `[a-zA-Z][a-zA-Z0-9_]{0,47}`
  # (leading letter, alphanumerics + underscores only, max 48 chars; dashes
  # are NOT allowed and the cap is 48, not 100 as RESEARCH.md §6 Q4 assumed).
  # We derive the runtime name from name_prefix by replacing dashes with
  # underscores and truncating to 48 chars.
  runtime_name = substr(replace("${local.name_prefix}_runtime", "-", "_"), 0, 48)

  # AgentCore Memory names share the Runtime regex `[a-zA-Z][a-zA-Z0-9_]{0,47}`
  # (verified against the CreateMemory API; see RESEARCH.md §8). Same
  # underscores-only, 48-char-cap constraint — we apply the same dash→underscore
  # replacement and truncation as for the Runtime name to derive a Memory name
  # from `name_prefix`. For `environment=demo` this resolves to
  # `graphia_demo_memory` (19 chars, well within the cap).
  memory_name = substr(replace("${local.name_prefix}_memory", "-", "_"), 0, 48)

  # Career-stats long-term-Memory name (spec 006 / ADR 008). Per ADR 008 career
  # stats live on a SEPARATE AgentCore Memory from the diary tier — distinct
  # lifecycle, distinct retention semantics, and the self-managed strategy
  # binds here, not on the diary Memory. Same regex/cap as `memory_name`; for
  # `environment=demo` resolves to `graphia_demo_career_memory` (26 chars).
  career_memory_name = substr(replace("${local.name_prefix}_career_memory", "-", "_"), 0, 48)

  # AgentCore Gateway names per the CreateGateway API: same family as Memory's
  # `name` pattern — `[a-zA-Z][a-zA-Z0-9-_]{0,99}` (alphanumerics + `-_`, max
  # 100 chars). Gateway is **more permissive than Runtime**: dashes are
  # allowed, and the cap is 100, not 48. We keep the dash form for readability
  # since `name_prefix` already uses dashes. For `environment=demo` this
  # resolves to `graphia-demo-gateway` (20 chars, well within cap). See
  # RESEARCH.md §9 for the constraint source.
  gateway_name = substr("${local.name_prefix}-gateway", 0, 100)

  # CloudWatch log group path for AgentCore Runtime traces / logs. AgentCore
  # writes to /aws/bedrock-agentcore/* by default; we use a Graphia-namespaced
  # subpath so multiple environments in the same account stay separated.
  runtime_log_group = "/aws/bedrock-agentcore/${local.name_prefix}-runtime"

  # Career-stats long-term-Memory namespace (spec 006 / ADR 007 — Layer 1).
  # The self-managed strategy's records live here; `ListMemoryRecords` reads by
  # this namespace deterministically. Identity is stable across games
  # (actor_id="human-career", NOT the per-game player id), so the namespace is
  # constant. MUST match the app's `GRAPHIA_STATS_NAMESPACE` default
  # (`src/graphia/config.py`, technical-considerations §2.2) so local-mode and
  # remote-mode read/write the same logical career bucket.
  stats_namespace = "/career/human-career/"

  # S3 bucket receiving the self-managed strategy's batched event payloads
  # (RESEARCH.md §14). Bucket names are global, lowercase, dash-only, <= 63
  # chars: `name_prefix` is already lowercase-dash, and the account id keeps the
  # name globally unique without leaking anything secret. For environment=demo
  # this resolves to `graphia-demo-stats-payload-257394491982` (well under 63).
  stats_payload_bucket = substr("${local.name_prefix}-stats-payload-${data.aws_caller_identity.current.account_id}", 0, 63)

  # SNS topic for the self-managed strategy's job notifications (RESEARCH.md
  # §14). Standard (non-FIFO) topic: career records are written on demand, not
  # driven by the never-fired auto-extraction trigger, so per-session ordering
  # is irrelevant.
  stats_topic_name = "${local.name_prefix}-stats-payload"

  # Bedrock model ARN patterns the Runtime is allowed to invoke. Graphia
  # calls Amazon Nova foundation models directly (Nova Pro for gameplay,
  # Nova Lite for roster generation) — pinned to ${var.region}.
  #
  # Why Nova instead of Anthropic Claude: across the entire current Claude
  # family on Bedrock, only Claude 3 Haiku (`20240307`) still supports
  # direct on-demand invocation in us-east-1. Claude 3 Sonnet is legacy +
  # inactive, Claude 3 Opus / 3.5 Sonnet (v1, v2) / 3.7 Sonnet are
  # end-of-life, Claude 3.5 Haiku + Claude 4.x are inference-profile-only.
  # Going via the `us.*` system profile would solve the on-demand
  # constraint but pulls in cross-region routing (us-east-2, us-west-2)
  # where the role can't auto-subscribe via Marketplace. Nova models
  # support direct on-demand in us-east-1 today, no profile required.
  bedrock_invoke_resources = [
    "arn:aws:bedrock:${var.region}::foundation-model/amazon.nova-*",
  ]
}
