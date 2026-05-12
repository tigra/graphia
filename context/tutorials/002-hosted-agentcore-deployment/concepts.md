---
spec: 002-hosted-agentcore-deployment
spec_title: Hosted AgentCore Deployment (interim — Slices 1–3)
introduced_on: 2026-05-12
---

# Concepts introduced in this increment

## AgentCore deployment surface

- **Managed-container Runtime as the deployment target** (`agentcore-runtime-as-container`) — AgentCore Runtime is a scale-to-zero, `linux/arm64`-only managed container surface; the deployable unit is a container image in ECR.
- **Bedrock-AgentCore Python entrypoint** (`bedrock-agentcore-entrypoint-decorator`) — A `BedrockAgentCoreApp` instance plus a function decorated with `@app.entrypoint` plus an `app.run()` call is the full Python contract; the SDK bundles Starlette + Uvicorn internally.
- **Explicit host binding for Podman compatibility** (`runtime-bind-explicit-host`) — `app.run(host="0.0.0.0")` overrides the SDK's `/.dockerenv` auto-detect heuristic, which would otherwise bind localhost-only on container runtimes that don't create the marker file.

## Bedrock / IAM auth inside a managed workload

- **Workload credentials via IAM execution role** (`agentcore-iam-execution-role`) — The Runtime assumes an IAM role with trust principal `bedrock-agentcore.amazonaws.com` and a least-privilege inline policy; the SDK exposes the role's credentials via the standard environment variables so boto3's default chain picks them up.
- **Standard-credential-chain auth in `GraphiaConfig`** (`boto3-default-credential-chain`) — `GraphiaConfig.load_config()` no longer requires a bearer token; auth flows through boto3's default chain (`AWS_PROFILE` / SSO / instance role). Bearer-token is kept as an optional legacy fallback. Profile name never hardcoded in source.

## Infrastructure as Code

- **AgentCore resources in the AWS provider** (`agentcore-runtime-tf-resource-quirks`) — `aws_bedrockagentcore_agent_runtime` (note the `_agent_` infix in the Terraform name, even though CloudFormation calls it `AWS::BedrockAgentCore::Runtime`); the resource exposes no `invocation_url` attribute — clients invoke against the `agentRuntimeArn` via the `InvokeAgentRuntime` data-plane API.
- **Required tags via provider `default_tags`** (`provider-default-tags-block`) — One `local.common_tags` map (`Project`, `ManagedBy`, `Environment`, `Owner`) applied to every taggable resource by the provider; no per-resource `tags = …` repetition.
- **Single name prefix with regex-aware variants** (`name-prefix-with-regex-aware-variants`) — All resource names derive from `local.name_prefix = "graphia-${var.environment}"`; resources with stricter regexes (AgentCore Runtime requires underscores) get derived variants via `replace()` + `substr()`.
- **ECR force-delete safeguard** (`ecr-force-delete-safeguard`) — `var.ecr_force_delete` defaults to `false` so destroy refuses to drop the ECR repo while it has images; override is a two-step (targeted apply, then destroy) because the AWS provider reads `force_delete` from prior state at destroy time.
- **Terraform run inside a pinned container** (`terraform-via-pinned-container`) — A `./tf` wrapper auto-detects Podman or Docker, pulls a pinned `hashicorp/terraform:1.13.1` image, mounts the project + SSO cache, and forwards `AWS_PROFILE`. Removes "works on my Terraform version" drift.

## Container build & deploy loop

- **Multi-stage uv-driven Dockerfile** (`multi-stage-uv-dockerfile`) — uv-based builder stage installs deps from `pyproject.toml` + `uv.lock` before copying source, so source edits don't bust the dep-install cache. Final stage copies the `.venv` only.
- **Makefile as project-wide task-runner** (`makefile-as-task-runner`) — Repo-root Makefile orchestrates `./tf` + container-runtime + AWS CLI under named workflow composites (`make deploy`, `make redeploy`, `make destroy`); defaults pull from `git config user.email` and `git rev-parse --short HEAD`.
- **Image-driven Runtime deploys** (`image-driven-deploys`) — The AgentCore Runtime resource interpolates `container_uri = "<repo>:${var.image_tag}"`, so bumping the tag string is what triggers a roll. `make redeploy` always passes the current git SHA as the tag.
- **Bootstrap-then-apply first deploy** (`bootstrap-then-apply`) — First-time deploy chicken-and-egg: a targeted apply (`./tf apply -target=aws_ecr_repository.runtime`) creates only the ECR repo so `make push` has somewhere to push to; a full apply follows.

## Run-mode plumbing (seam for Slice 4)

- **Dual-mode config with contradiction check** (`dual-mode-config-with-contradiction-check`) — `GraphiaConfig` gains `remote_mode` and `runtime_invocation_url` fields; `load_config()` raises only when the state is inconsistent (remote requested but no URL), not when any single auth path is configured.
- **Argparse → env → typed config bridge** (`argparse-env-bridge`) — `--remote` is parsed by argparse, then promoted via `os.environ["GRAPHIA_REMOTE"] = "1"` *before* `load_config()` runs — so the typed dataclass remains the single source of truth, no parallel "is the flag set?" check.