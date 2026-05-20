# Graphia Hosted AgentCore — Terraform Module

Root Terraform module that provisions Graphia's hosted AgentCore stack
(Runtime, Gateway, Memory, Observability) in AWS. Backs the
`uv run python -m graphia --remote` play mode.

Spec: [`context/spec/002-hosted-agentcore-deployment/`](../../context/spec/002-hosted-agentcore-deployment/)

optcoThis README is a follow-the-steps guide for a fresh contributor:
[pre-flight](#pre-flight-checklist) → [first-time deploy](#first-time-deploy) →
[redeploy](#steady-state-redeploy) → [observability setup](#observability-one-time-setup)
→ [destroy](#destroy-procedure) → [troubleshooting](#troubleshooting).

## What gets provisioned

The module provisions the full hosted AgentCore stack:

- **ECR repository** (`<name_prefix>-runtime`) — holds the Graphia Runtime
  container image. Scan-on-push enabled; tag mutability is `MUTABLE` to
  allow dev re-pushes of `:latest`.
- **CloudWatch log group** (`/aws/bedrock-agentcore/<name_prefix>-runtime`,
  30-day retention) — receives Runtime traces and logs, plus the
  vended-log-delivery pipeline (`aws_cloudwatch_log_delivery_*`) that routes
  application logs and OpenTelemetry traces into observability.
- **IAM execution role** (`<name_prefix>-runtime`) — assumed by the
  `bedrock-agentcore.amazonaws.com` service principal. Inline policy grants
  the minimum needed: ECR pull on the repo above, Bedrock
  `InvokeModel` / `Converse` against the US regional Sonnet + Haiku
  inference profiles and their foundation-model ARNs, CloudWatch log group /
  stream creation under `/aws/bedrock-agentcore/runtimes/*`, X-Ray trace
  export, and CloudWatch metric publishing (see RESEARCH.md §13).
- **AgentCore Runtime** (`aws_bedrockagentcore_agent_runtime`) — references
  the ECR image URI (`<repository_url>:${var.image_tag}`) and the execution
  role. Network mode is `PUBLIC`; VPC mode is out of scope for spec 002.
- **AgentCore Memory** (`aws_bedrockagentcore_memory`) — stores per-game
  diary entries and long-term cross-game stats.
- **AgentCore Gateway + Gateway targets** — fronts the Lambda-backed diary
  tools (ADR 005).
- **Transaction Search resource policy** — `aws_cloudwatch_log_resource_policy`
  letting X-Ray write spans to CloudWatch Logs (step 1 of Transaction Search;
  steps 2–3 are out-of-band — see [Observability](#observability-one-time-setup)).

**Image-push prerequisite:** the Runtime resource references an image URI
in ECR. `terraform plan` does not validate that the image exists, but
`terraform apply` will fail to create the Runtime if the tag has not been
pushed. The whole workflow — image build, ECR auth, push, and Terraform
apply — is wrapped in the repo-root `Makefile`; the canonical end-to-end
target is `make deploy` (see [First-time deploy](#first-time-deploy)).

## Tooling model

**`./tf` is the tool wrapper; the `Makefile` is the workflow front door.**

- The module never invokes `terraform` directly — every command goes through
  the `./tf` wrapper, which runs `hashicorp/terraform:1.13.1` inside your
  chosen container runtime (Podman or Docker). This guarantees every
  developer and CI executes against the exact same Terraform + provider
  versions.
- Day-to-day, prefer the repo-root **Makefile targets** (`make deploy`,
  `make redeploy`, `make tf-plan`, etc.). They fill in `environment`,
  `owner` (from `git config user.email`), and `image_tag` (from
  `git rev-parse --short HEAD`) automatically.
- `./tf` direct invocation is reserved for inspection (`./tf show`,
  `./tf state list`, `./tf output …`, `./tf force-unlock …`) and
  escape-hatch operations not covered by a Makefile target.

Run `make help` at the repo root for the full target list.

## Pre-flight checklist

Before the first deploy, confirm each of the following.

1. **A container runtime — Podman or Docker.** Both are first-class
   supported; whichever you have installed is fine. The `./tf` wrapper and
   the `make build` image build auto-detect which one is present.
   - Podman: <https://podman.io/docs/installation>
   - Docker: <https://docs.docker.com/engine/install/>
   - Verify: `podman info` (or `docker info`) succeeds.
2. **`uv`** — drives the Python build (`make build-lambdas`, `make play`).
   - Install: <https://docs.astral.sh/uv/getting-started/installation/>
3. **AWS CLI v2** — used by `make login-ecr`, `make wire-env`, and
   `make enable-transaction-search`.
   - Install: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
4. **An active AWS SSO session.** Configure your profile once (`aws configure
   sso` for SSO accounts, or `aws configure` for static credentials) so that
   `~/.aws/config` knows about it, then set `AWS_PROFILE` in `.env` (or export
   it in your shell):

   ```bash
   aws sso login --profile <your-profile>
   export AWS_PROFILE=<your-profile>
   ```

   The profile name is never baked into source — any profile pointing at a
   Bedrock-enabled account works. The AWS account ID is derived from the
   profile at apply/build time (`aws sts get-caller-identity` in the Makefile,
   `data.aws_caller_identity.current` in Terraform), so there is no separate
   `AWS_ACCOUNT` env var to set. SSO tokens cache under `~/.aws/sso/cache/`
   and expire after ~8 hours; re-run `aws sso login` when they lapse.
5. **Required Terraform variables.** Two variables have no default and must
   be supplied on every apply/destroy:
   - `environment` — deployment environment name (e.g. `demo`, `dev`,
     `prod`). The Makefile defaults this to `demo` via the `ENVIRONMENT`
     variable; override with `make tf-apply ENVIRONMENT=dev`.
   - `owner` — owner of the deployment (typically the developer email). The
     Makefile defaults this to `git config user.email` via the `OWNER`
     variable.
   - `image_tag` is **git-SHA-driven** — the Makefile sets it from
     `git rev-parse --short HEAD`. You do not pass it by hand; just commit
     before deploying (see [Steady-state redeploy](#steady-state-redeploy)).

## First-time deploy

The Runtime resource pulls its ECR image **at create time**, so the very
first deploy cannot be a single `terraform apply` — the ECR repository must
exist and be populated *before* the Runtime resource is created. The
`make deploy` composite handles this ordering for you:

```bash
# From the project root, with AWS_PROFILE exported (see pre-flight).
make deploy
```

`make deploy` chains, in order:

| Step | Target | What it does |
| ---- | ------ | ------------ |
| 1 | `build-lambdas` | Pip-installs + zips the diary-tool Lambda functions (ADR 005). |
| 2 | `tf-init` | Initialises the Terraform module inside the `./tf` container. |
| 3 | `tf-ecr-bootstrap` | Targeted apply of **only** `aws_ecr_repository.runtime` — creates the empty ECR repo. |
| 4 | `push` | Builds the Runtime image, logs into ECR, pushes `:<git-sha>` and `:latest`. |
| 5 | `tf-apply` | Full apply — now the Runtime resource can pull the image that step 4 pushed. |
| 6 | `wire-env` | Writes the deployment's outputs into `.env`. |

**Why the bootstrap-then-apply two-step exists:** if `tf-apply` ran before
the image was pushed, the AgentCore Runtime resource would fail to create —
it pulls `<repository_url>:<image_tag>` during `CreateAgentRuntime`. The
targeted `tf-ecr-bootstrap` creates just the repo so step 4 has somewhere to
push to; the full `tf-apply` in step 5 then creates the Runtime against a
populated repository.

**`wire-env` output.** Step 6 reads the Terraform outputs and writes three
keys into the repo-root `.env`, replacing any existing lines in place and
preserving everything else:

- `GRAPHIA_RUNTIME_URL` — the Runtime invocation target for `--remote` play.
- `GRAPHIA_MEMORY_ID` — the AgentCore Memory id for diary read/write.
- `GRAPHIA_LOG_GROUP` — the CloudWatch log group for observability tooling.

After `make deploy` completes, `make play-remote` plays a game against the
deployed Runtime. `make tf-output` (or `cd infra/terraform && ./tf output`)
names the Runtime ARN, log group, ECR repository, and resolved image URI.

If you need to run the steps individually (e.g. to debug a stuck composite),
each is its own target: `make build-lambdas`, `make tf-init`,
`make tf-ecr-bootstrap`, `make push`, `make tf-apply`, `make wire-env`.

## Steady-state redeploy

After the first deploy, code-change cycles use the steady-state composite:

```bash
make redeploy
```

`make redeploy` chains `build-lambdas → push → tf-apply → wire-env` — it
skips `tf-init` and `tf-ecr-bootstrap` (the repo already exists).

**Commit before you redeploy.** The image tag is the **git short SHA**
(`git rev-parse --short HEAD`). Bumping `image_tag` is what tells the
AgentCore Runtime to roll the deployment — pushing a new image under the
*same* SHA is a Terraform-side no-op and the Runtime will keep serving the
old code. If you have uncommitted changes, commit them first so the SHA
advances; otherwise `tf-apply` sees no change to `image_tag` and the Runtime
does not roll.

## Observability one-time setup

AgentCore Observability has two halves (RESEARCH.md §12):

1. **Per-Runtime vended log delivery** — Terraform-managed. The
   `aws_cloudwatch_log_delivery_*` resources route Runtime application logs
   and OpenTelemetry traces into CloudWatch. Created by `make deploy` /
   `make redeploy`; nothing extra to do.
2. **Account-level CloudWatch Transaction Search** — *not* fully
   Terraform-expressible. Step 1 (the X-Ray → CloudWatch Logs resource
   policy) **is** in the module. Steps 2–3 (`UpdateTraceSegmentDestination`,
   `UpdateIndexingRule`) have no resource in `hashicorp/aws = 6.44.0`, so
   they run out-of-band:

   ```bash
   make enable-transaction-search
   ```

This is a **one-time, per-AWS-account** setup — run it once, after the first
`make deploy` (the target is idempotent, so re-running is harmless). It
points X-Ray trace segments at CloudWatch Logs and sets span indexing to
100% (Graphia is low-traffic; the AWS default of 1% would leave most games
unindexed and absent from the GenAI Observability Sessions view).

Until Transaction Search is enabled, application logs still reach the log
group correctly — only the searchable **trace/span** tree in the GenAI
Observability console depends on it. See RESEARCH.md §12–§13 for the full
provider-gap analysis and the Runtime execution-role IAM requirements.

`make verify-observability` drives the deployed Runtime with a scripted
partial game and reports the real CloudWatch telemetry it recorded — the
iteration loop for observability work.

## Destroy procedure

```bash
make destroy        # alias for tf-destroy
```

`make destroy` removes all AgentCore resources for the current
`ENVIRONMENT` / `OWNER`.

**ECR `force_delete` safeguard.** By default `ecr_force_delete = false`, and
`terraform destroy` will **refuse to delete the ECR repository while it
still contains images** — a guard against accidental image loss. To purge
the images alongside the repo you must enable the flag.

Because the AWS provider reads `force_delete` from **prior state** at
destroy time (not from the current config or a `-var` on the destroy
command), enabling it is a **two-step** operation:

```bash
# 1. Targeted apply to flip ecr_force_delete=true in state for the ECR resource.
cd infra/terraform
./tf apply -target=aws_ecr_repository.runtime \
           -var environment=demo \
           -var owner="$(git config user.email)" \
           -var ecr_force_delete=true
cd ../..

# 2. Destroy with the override still set (the destroy now reads true from state).
make tf-destroy ECR_FORCE_DELETE=true
```

**AgentCore Memory data on destroy.** Destroying the module destroys the
`aws_bedrockagentcore_memory` resource, and **that drops every per-game
diary record stored in it** — no manual cleanup is needed or possible. The
Memory resource ARN is the only handle records are addressable through;
once the resource is gone, the records have no parent and are unreachable
(`DeleteMemory` cascades by construction — RESEARCH.md §6 Q1). Spec 002's
acceptance criterion "all data stored in AgentCore Memory is removed by
`terraform destroy`" holds with no pre-destroy hook.

**Manual cleanup that genuinely remains.** AgentCore creates one log group
that Terraform does **not** manage and therefore does **not** remove:

- `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` — the platform
  *creates this container log group itself* on the Runtime's first boot
  (RESEARCH.md §13). It is not in Terraform state, so `terraform destroy`
  leaves it behind. The `aws/spans` log group (Transaction Search span
  store) is likewise account-managed and lingers after destroy.

Neither lingering group holds Graphia game data (diary records live in
Memory, which *is* cleaned up). They are empty/low-cost CloudWatch log
groups; delete them by hand if you want a fully clean account:

```bash
aws logs delete-log-group \
  --log-group-name "/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT"
```

The Terraform-managed `/aws/bedrock-agentcore/<name_prefix>-runtime` group
*is* removed by `terraform destroy` — only the platform-created
`runtimes/<id>-DEFAULT` group and `aws/spans` linger.

## Troubleshooting

- **SSO session expired** — symptoms include `ExpiredToken`,
  `UnauthorizedException`, or `./tf` / `aws` calls failing to authenticate.
  Re-authenticate: `aws sso login --profile <your-profile>`. Tokens cache
  under `~/.aws/sso/cache/` and expire after ~8 hours. `make wire-env`,
  `make push`, and every `./tf` call need a live session.

- **Stale Terraform state lock** — a `make tf-apply` / `tf-destroy`
  interrupted mid-run (Ctrl-C, a killed `./tf` container, or an interactive
  `./tf apply` left running with no TTY) can leave the state lock held. The
  next command fails with `Error acquiring the state lock` and a lock ID.
  Release it:

  ```bash
  cd infra/terraform && ./tf force-unlock <lock-id>
  ```

  Only force-unlock once you are sure no `./tf` container is still running
  (`podman ps` / `docker ps`). Note that an interactive `./tf apply` started
  without a TTY can sit waiting for confirmation input it never gets while
  still holding the lock — kill that container before unlocking.

- **ECR push fails with `broken pipe` / `unexpected EOF`** — a transient
  network hiccup during a layer upload. **Retry** — `podman push` (and
  `docker push`) resume from already-uploaded layers, so `make push` /
  `make redeploy` a second time usually completes. No cleanup needed.

- **Runtime not picking up new code after a redeploy** — the image tag is
  the git short SHA. If you pushed without committing, the SHA is unchanged,
  `tf-apply` sees no `image_tag` delta, and the Runtime keeps serving the
  old image. **Commit your changes**, then `make redeploy` so the SHA — and
  thus `image_tag` — advances and the Runtime rolls.

- **`terraform apply` fails creating the AgentCore Runtime** — usually the
  ECR image for the tag was never pushed (`tf-apply` ran before `push`). Use
  `make deploy` for the first deploy (it bootstraps ECR and pushes before
  the full apply); for later cycles use `make redeploy`. Confirm the tag
  exists with `aws ecr describe-images --repository-name graphia-<env>-runtime`.
  Note the ECR repository does **not** survive a force-delete `terraform
  destroy`, so a destroy → re-deploy cycle must rebuild and re-push the image
  (`make deploy` / `make redeploy`), not `make tf-apply`.

- **Remote game hits the *old* Runtime after a destroy / re-apply** — symptom:
  a `--remote` game fails with `ResourceNotFoundException ... No endpoint or
  agent found with qualifier 'DEFAULT'` naming a Runtime ARN that no longer
  exists. A destroy + re-apply gives the new Runtime a fresh ARN suffix;
  `make redeploy`'s `wire-env` writes it into `.env`, but if `GRAPHIA_RUNTIME_URL`
  (or `GRAPHIA_MEMORY_ID` / `GRAPHIA_LOG_GROUP`) is also **exported in your
  shell**, that stale value shadows `.env` — `python-dotenv` does not override
  variables already in the environment. Fix: `unset GRAPHIA_RUNTIME_URL
  GRAPHIA_MEMORY_ID GRAPHIA_LOG_GROUP` so `.env` wins, then `make play-remote`.

- **`./tf` fails with "No container runtime found"** — install either Podman
  (<https://podman.io/docs/installation>) or Docker
  (<https://docs.docker.com/engine/install/>); both are equally supported.

- **Provider download fails during `tf-init`** — run `make tf-init` again,
  or `cd infra/terraform && ./tf init -upgrade` to refresh the provider
  plugin cache inside the container.

- **Podman on SELinux hosts: volume-mount permission errors** — rootless
  Podman may need the `:Z` flag on volume mounts. Adapt the `./tf` wrapper's
  volume flags locally, or run rootful (less preferred).

- **Docker: "Cannot connect to the Docker daemon"** — ensure the daemon is
  running (`docker info` should succeed). On macOS/Windows that means Docker
  Desktop / OrbStack / Colima is launched; on Linux, the `docker` service.

- **Empty / flat trace tree in the GenAI Observability console** — two
  causes. (1) Transaction Search is not enabled: run
  `make enable-transaction-search` once per account. (2) The Runtime
  execution role is missing X-Ray / log-group IAM grants (RESEARCH.md §13) —
  re-apply the module so the inline policy is current; IAM is evaluated at
  call time, so the next invocation picks the grants up with no image
  rebuild.

- **AgentCore provider gaps** — RESEARCH.md documents the known
  `hashicorp/aws = 6.44.0` gaps and how the module works around them:
  Gateway-target IAM credential provider (§10), CloudWatch Transaction
  Search steps 2–3 (§12), and the full Runtime execution-role observability
  IAM action set (§13). Consult RESEARCH.md before assuming a missing field
  is a module bug.

## Inputs

| Name          | Type   | Default               | Description                                              |
| ------------- | ------ | --------------------- | -------------------------------------------------------- |
| `region`      | string | `us-east-1`           | AWS region into which the AgentCore stack is deployed.   |
| `environment` | string | _required_            | Deployment environment name (e.g. `demo`, `dev`, `prod`).|
| `owner`       | string | _required_            | Owner of the deployment (typically the developer email). |
| `agent_id`    | string | `graphia-mafia-agent` | Logical agent identifier for memory namespacing.         |
| `image_tag`   | string | `latest`              | ECR image tag for the Runtime container; the Makefile overrides it with the git short SHA at apply time. |
| `ecr_force_delete` | bool | `false`            | Safeguard: if `false`, `terraform destroy` refuses to delete the ECR repo while it has images. Set `true` (via the two-step destroy — see [Destroy procedure](#destroy-procedure)) for intentional teardown. |

## Outputs

| Name                         | Description                                                |
| ---------------------------- | ---------------------------------------------------------- |
| `runtime_invocation_url`     | AgentCore Runtime ARN (clients invoke via `InvokeAgentRuntime`); `wire-env` writes it to `.env` as `GRAPHIA_RUNTIME_URL`. |
| `runtime_execution_role_arn` | IAM role ARN the Runtime assumes at boot.                  |
| `ecr_repository_url`         | ECR repository URL (push destination for the Runtime image).|
| `ecr_image_uri`              | Full ECR image URI including the resolved `image_tag`.     |
| `cloudwatch_log_group`       | CloudWatch log group receiving Runtime traces / logs; `wire-env` writes it to `.env` as `GRAPHIA_LOG_GROUP`. |
| `cloudwatch_log_group_arn`   | ARN of the CloudWatch log group above.                     |
| `memory_id`                  | AgentCore Memory id; `wire-env` writes it to `.env` as `GRAPHIA_MEMORY_ID`. |
| `memory_namespace`           | AgentCore Memory namespace.                                |
