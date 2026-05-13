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
  description = "CloudWatch log group name receiving AgentCore Runtime traces / logs."
  value       = aws_cloudwatch_log_group.runtime.name
}

output "memory_id" {
  description = "AgentCore Memory identifier passed to the Runtime via the `GRAPHIA_MEMORY_ID` env var. This is the value `MemoryClient.create_event(memory_id=...)` and `list_events(memory_id=...)` expect — surfaced here so local-mode workflows / debugging can target the same Memory without poking at the Runtime container."
  value       = aws_bedrockagentcore_memory.this.id
}
