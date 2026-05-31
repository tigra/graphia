output "runtime_invocation_url" {
  description = "ARN of the AgentCore Runtime. Clients invoke the runtime via the bedrock-agentcore `InvokeAgentRuntime` API using this ARN; the provider does not expose a separate invocation URL/endpoint attribute (see RESEARCH.md §6 addendum)."
  value       = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "runtime_execution_role_arn" {
  description = "ARN of the IAM role the AgentCore Runtime assumes at boot."
  value       = aws_iam_role.runtime.arn
}

output "ecr_repository_url" {
  description = "ECR repository URL (without tag). Use this as the `podman/docker push` destination — `Makefile` (Slice 3 sub-task 5) wires this into `make push`."
  value       = aws_ecr_repository.runtime.repository_url
}

output "ecr_image_uri" {
  description = "Fully-qualified ECR image URI for the Runtime container, including the resolved `image_tag` variable. This is the exact URI the AgentCore Runtime resource references."
  value       = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group name receiving AgentCore Runtime application logs via the vended-log-delivery pipeline."
  value       = aws_cloudwatch_log_group.runtime.name
}

output "cloudwatch_log_group_arn" {
  description = "ARN of the CloudWatch log group receiving AgentCore Runtime application logs. The vended-log-delivery destination points at this ARN; the Slice 8 failure-modal work and the USER smoke test use it to locate Runtime logs."
  value       = aws_cloudwatch_log_group.runtime.arn
}

output "memory_id" {
  description = "AgentCore Memory identifier passed to the Runtime via the `GRAPHIA_MEMORY_ID` env var. This is the value `MemoryClient.create_event(memory_id=...)` and `list_events(memory_id=...)` expect — surfaced here so local-mode workflows / debugging can target the same Memory without poking at the Runtime container."
  value       = aws_bedrockagentcore_memory.this.id
}

output "career_memory_id" {
  description = "Career-stats AgentCore Memory identifier passed to the Runtime via the `GRAPHIA_CAREER_MEMORY_ID` env var (ADR 008). Distinct from `memory_id` (the diary Memory): the Runtime writes per-action career events here, the consumer Lambda consolidates them into the long-term `CareerStats` record under the self-managed strategy's namespace, and `make create-stats-strategy` attaches the strategy to this Memory. Surfaced for local-mode / out-of-band tooling that needs to target the career bucket directly."
  value       = aws_bedrockagentcore_memory.career.id
}

output "stats_strategy_id" {
  description = "Id of the self-managed (custom) long-term-Memory strategy backing remote-mode career stats (spec 006 / ADR 007), passed to the Runtime via the `GRAPHIA_STATS_STRATEGY_ID` env var. This is the value `AgentCoreLongTermStatsStore` uses as `memoryStrategyId` when authoring/reading the career record by namespace. Echoes the `stats_strategy_id` var because the strategy is created OUT-OF-BAND (`make create-stats-strategy`) — provider 6.44.0 has no SELF_MANAGED strategy surface (RESEARCH.md §14). Empty until that command has run and its `strategyId` is fed back via `-var stats_strategy_id=...`."
  value       = var.stats_strategy_id
}

output "stats_namespace" {
  description = "Career-stats long-term-Memory namespace passed to the Runtime via the `GRAPHIA_STATS_NAMESPACE` env var. `AgentCoreLongTermStatsStore` lists/writes the rolling career record under this namespace deterministically (`ListMemoryRecords` by namespace, no semantic search). Matches the app's `GRAPHIA_STATS_NAMESPACE` default `/career/human-career/`; surfaced here so local-mode / out-of-band tooling target the same logical career bucket."
  value       = local.stats_namespace
}

output "stats_payload_bucket" {
  description = "Name of the S3 bucket the self-managed strategy delivers batched event payloads to. Input to the out-of-band `make create-stats-strategy` (`invocationConfiguration.payloadDeliveryBucketName`). Standing scaffolding required by a configured self-managed strategy even though the trigger is never fired (ADR 007 §5)."
  value       = aws_s3_bucket.stats_payload.id
}

output "stats_payload_topic_arn" {
  description = "ARN of the SNS topic the self-managed strategy publishes job notifications to. Input to the out-of-band `make create-stats-strategy` (`invocationConfiguration.topicArn`)."
  value       = aws_sns_topic.stats_payload.arn
}

output "memory_execution_role_arn" {
  description = "ARN of the IAM role the Memory service assumes for the self-managed strategy's S3/SNS payload delivery. Input to the out-of-band `make create-stats-strategy` (`--memory-execution-role-arn`); also set on the Memory resource as `memory_execution_role_arn`."
  value       = aws_iam_role.memory_stats.arn
}

output "gateway_id" {
  description = "AgentCore Gateway identifier passed to the Runtime via the `GRAPHIA_GATEWAY_ID` env var. Sub-task 3's in-container MCP client uses it to construct the Gateway invocation URL when routing diary calls through Gateway-MCP."
  value       = aws_bedrockagentcore_gateway.this.gateway_id
}

output "gateway_arn" {
  description = "AgentCore Gateway ARN — the scope of the Runtime IAM role's `bedrock-agentcore:InvokeGateway` permission. Useful for cross-account / cross-stack references and for the gateway resource-based-policy work Phase 7 will layer on."
  value       = aws_bedrockagentcore_gateway.this.gateway_arn
}

output "gateway_invocation_url" {
  description = "Gateway MCP URL — `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`. MCP clients (the agent inside the Runtime in sub-task 3; local-mode flows for debugging) point a streamable-HTTP MCP client at this URL using SigV4-signed requests against the gateway ARN."
  value       = aws_bedrockagentcore_gateway.this.gateway_url
}

output "diary_write_lambda_arn" {
  description = "ARN of the Gateway-fronted diary_write Lambda. Useful for ad-hoc smoke tests via `aws lambda invoke` outside the Gateway path."
  value       = aws_lambda_function.diary_write.arn
}

output "diary_read_lambda_arn" {
  description = "ARN of the Gateway-fronted diary_read Lambda."
  value       = aws_lambda_function.diary_read.arn
}
