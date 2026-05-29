variable "region" {
  description = "AWS region into which the AgentCore stack is deployed."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name (e.g. demo, dev, staging, prod). Used in resource naming and tags."
  type        = string
}

variable "owner" {
  description = "Owner of the deployment (typically the developer's email). Surfaced in the Owner tag on every taggable resource."
  type        = string
}

variable "agent_id" {
  description = "Logical agent identifier used to scope per-game and long-term memory namespaces."
  type        = string
  default     = "graphia-mafia-agent"
}

variable "image_tag" {
  description = "ECR image tag for the Runtime container. Defaults to `latest`; override at apply time with a git SHA (e.g. -var image_tag=$(git rev-parse --short HEAD)) for traceable deploys."
  type        = string
  default     = "latest"
}

variable "ecr_force_delete" {
  description = "If true, terraform destroy purges all images from the ECR repo alongside the repo. Default off as a safeguard against accidental image loss; override at destroy time (e.g. via `make tf-destroy ECR_FORCE_DELETE=true`)."
  type        = bool
  default     = false
}

variable "stats_strategy_id" {
  description = "Id of the self-managed (custom) long-term-Memory strategy backing remote-mode career stats (spec 006 / ADR 007). The strategy is created OUT-OF-BAND via `make create-stats-strategy` because provider 6.44.0's `aws_bedrockagentcore_memory_strategy` has no SELF_MANAGED / invocation_configuration surface (S3 + SNS payload delivery) — see RESEARCH.md §14. Feed the `strategyId` that command returns back in (e.g. via `make tf-apply STATS_STRATEGY_ID=...`) so it is plumbed to the Runtime as `GRAPHIA_STATS_STRATEGY_ID`. Empty until the strategy exists; the Runtime then resolves it by listing the Memory's strategies (technical-considerations §2.2)."
  type        = string
  default     = ""
}
