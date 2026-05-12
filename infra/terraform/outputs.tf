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

output "memory_namespace" {
  description = "AgentCore Memory namespace identifier used for per-game diary entries. Populated by Slice 6 once the Memory resource is declared."
  value       = null
}
