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

  # CloudWatch log group path for AgentCore Runtime traces / logs. AgentCore
  # writes to /aws/bedrock-agentcore/* by default; we use a Graphia-namespaced
  # subpath so multiple environments in the same account stay separated.
  runtime_log_group = "/aws/bedrock-agentcore/${local.name_prefix}-runtime"

  # Bedrock model / inference-profile ARN patterns the Runtime is allowed to
  # invoke. Graphia uses the US regional inference profiles
  # (us.anthropic.claude-sonnet-* and us.anthropic.claude-haiku-*) per the
  # project_aws_region memory. Foundation-model ARNs are account-less; the
  # inference-profile ARNs include the account ID.
  bedrock_invoke_resources = [
    "arn:aws:bedrock:${var.region}::foundation-model/anthropic.claude-sonnet-*",
    "arn:aws:bedrock:${var.region}::foundation-model/anthropic.claude-haiku-*",
    "arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/us.anthropic.claude-sonnet-*",
    "arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/us.anthropic.claude-haiku-*",
  ]
}
