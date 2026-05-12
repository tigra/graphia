# Graphia Hosted AgentCore — Terraform Module

Root Terraform module that provisions Graphia's hosted AgentCore stack
(Runtime, Gateway, Memory, Observability) in AWS. Backs the
`uv run python -m graphia --remote` play mode.

Spec: [`context/spec/002-hosted-agentcore-deployment/`](../../context/spec/002-hosted-agentcore-deployment/)

## What gets provisioned

As of Slice 3 sub-task 4 the module provisions the AgentCore Runtime stack:

- **ECR repository** (`<name_prefix>-runtime`) — holds the Graphia Runtime
  container image. Scan-on-push enabled; tag mutability is `MUTABLE` to
  allow dev re-pushes of `:latest`.
- **CloudWatch log group** (`/aws/bedrock-agentcore/<name_prefix>-runtime`,
  30-day retention) — receives Runtime traces and logs. Slice 8 will layer
  metric filters / alarms on top.
- **IAM execution role** (`<name_prefix>-runtime`) — assumed by the
  `bedrock-agentcore.amazonaws.com` service principal. Inline policy grants
  the minimum needed at boot: ECR pull on the repo above, Bedrock
  `InvokeModel` / `InvokeModelWithResponseStream` against the US regional
  Sonnet + Haiku inference profiles and their underlying foundation-model
  ARNs, and CloudWatch log write into the log group above.
- **AgentCore Runtime** (`aws_bedrockagentcore_agent_runtime`) — references
  the ECR image URI (`<repository_url>:${var.image_tag}`) and the execution
  role. Network mode is `PUBLIC`; VPC mode is out of scope for spec 002.

Slice 6 adds the AgentCore Memory resource (the `memory_namespace` output
is `null` until then).

**Image-push prerequisite:** the Runtime resource references an image URI
in ECR. `terraform plan` does not validate that the image exists, but
`terraform apply` will fail to create the Runtime if the tag has not been
pushed. The build-and-push workflow is wrapped in the repo-root `Makefile`
(see `make help`); the canonical bootstrap-then-apply sequence is documented
in the [Apply procedure](#apply-procedure) below.

## Prerequisites

- **A container runtime — Podman or Docker.** Both are first-class supported;
  whichever you prefer is fine. The included `./tf` wrapper auto-detects which
  one you have installed and uses it.
  - Podman: <https://podman.io/docs/installation>
  - Docker: <https://docs.docker.com/engine/install/>
- **AWS CLI v2** with an active SSO session for the AWS profile you configure.
  The project's documented default profile is `my-aws-profile`, but any
  profile pointing at a Bedrock-enabled account works.

The module never invokes `terraform` directly — every command goes through
the `./tf` wrapper, which runs `hashicorp/terraform:1.13.1` inside your
chosen container runtime. This guarantees every developer (and CI) executes
against the exact same Terraform + provider versions.

## Apply procedure

The Runtime resource depends on an ECR image. The image is built from the
project root via the repo-root `Makefile` (`make build` / `make push`) and
pushed to the ECR repo this module creates. Because the repo doesn't exist
before first apply, the bootstrap sequence is three steps:

```bash
# From the project root throughout.

# 0. Authenticate against your SSO profile (and export it for the credential chain).
aws sso login --profile <your-profile>
export AWS_PROFILE=<your-profile>

# 1. Provision just the ECR repo so we have somewhere to push to.
cd infra/terraform
./tf init
./tf apply -target=aws_ecr_repository.runtime \
           -var environment=demo -var owner=<your-email>
cd ../..

# 2. Build the image and push it (tagged by git SHA + :latest).
make push
# (Override-friendly: make push ENVIRONMENT=demo AWS_ACCOUNT=... AWS_REGION=...)

# 3. Apply the rest of the module with the same git-SHA image tag.
cd infra/terraform
./tf apply -var environment=demo \
           -var owner=<your-email> \
           -var image_tag=$(git -C ../.. rev-parse --short HEAD)
```

Subsequent updates only need steps 2 + 3 — `make push` then `./tf apply
-var image_tag=$(git rev-parse --short HEAD)`. Bumping the tag is what
tells AgentCore Runtime to roll the deployment; pushing the same tag string
without changing `var.image_tag` is a no-op from Terraform's perspective.

After apply succeeds, `./tf output` names the deployed Runtime's ARN, the
CloudWatch log group receiving observability traces, the ECR repository,
and the resolved image URI.

## Destroy procedure

```bash
./tf destroy -var environment=demo -var owner=<your-email>
```

Destroying the module removes all AgentCore resources, including the
per-game diary entries stored in AgentCore Memory (the parent Memory
resource is the only handle records are addressable through — once it's
gone, records are unreachable). See RESEARCH.md §6 Q1.

## Troubleshooting

- **SSO session expired.** Re-run `aws sso login --profile <your-profile>`.
  AWS SSO tokens cache under `~/.aws/sso/cache/` and expire after ~8 hours
  by default.
- **Provider download fails.** Try `./tf init -upgrade` to refresh the
  provider plugin cache inside the container.
- **`./tf` fails with "No container runtime found".** Install either Podman
  (<https://podman.io/docs/installation>) or Docker
  (<https://docs.docker.com/engine/install/>) — both are equally supported.
- **Podman on SELinux hosts: volume mounts fail with permission errors.**
  Rootless Podman may need the `:Z` flag on volume mounts on SELinux-enabled
  hosts. If you hit this, set `TERRAFORM_IMAGE` and adapt the wrapper's
  volume flags locally, or run rootful (less preferred).
- **Docker: wrapper fails with "Cannot connect to the Docker daemon".**
  Ensure the Docker daemon is running — `docker info` should succeed before
  calling `./tf`. On macOS/Windows that means Docker Desktop (or OrbStack /
  Colima) is launched; on Linux that means the `docker` service is started.

## Inputs

| Name          | Type   | Default               | Description                                              |
| ------------- | ------ | --------------------- | -------------------------------------------------------- |
| `region`      | string | `us-east-1`           | AWS region into which the AgentCore stack is deployed.   |
| `account_id`  | string | `123456789012`        | AWS account ID that owns the AgentCore stack.            |
| `environment` | string | _required_            | Deployment environment name (e.g. `demo`, `dev`, `prod`).|
| `owner`       | string | _required_            | Owner of the deployment (typically the developer email). |
| `agent_id`    | string | `graphia-mafia-agent` | Logical agent identifier for memory namespacing.         |
| `image_tag`   | string | `latest`              | ECR image tag for the Runtime container; override with a git SHA at apply time. |

## Outputs

| Name                         | Description                                                |
| ---------------------------- | ---------------------------------------------------------- |
| `runtime_invocation_url`     | AgentCore Runtime ARN (clients invoke via `InvokeAgentRuntime`). |
| `runtime_execution_role_arn` | IAM role ARN the Runtime assumes at boot.                  |
| `ecr_repository_url`         | ECR repository URL (push destination for the Runtime image).|
| `ecr_image_uri`              | Full ECR image URI including the resolved `image_tag`.     |
| `cloudwatch_log_group`       | CloudWatch log group receiving Runtime traces / logs.      |
| `memory_namespace`           | AgentCore Memory namespace (populated by Slice 6).         |
