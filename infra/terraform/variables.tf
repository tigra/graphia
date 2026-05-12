variable "region" {
  description = "AWS region into which the AgentCore stack is deployed."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "AWS account ID that owns the AgentCore stack. Defaults to the Graphia demo account."
  type        = string
  default     = "123456789012"
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
