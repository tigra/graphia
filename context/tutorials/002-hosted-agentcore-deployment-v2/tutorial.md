# Tutorial 002: Hosted AgentCore Deployment (interim — Slices 1–4)

- **Spec:** [`context/spec/002-hosted-agentcore-deployment/`](../../spec/002-hosted-agentcore-deployment/)
- **Status:** Draft
- **Author:** Alexey Tigarev
- **Date:** 2026-05-13
- **Prerequisites:** [`001-playable-skeleton`](../001-playable-skeleton/tutorial.md)

> **Heads-up — interim tutorial v2.** Spec 002 has 10 vertical slices; this tutorial covers what Slices 1–4 have delivered. Slices 5–10 (corner badge, AgentCore Memory, Gateway, Observability + failure modal, equivalence tests, destroy verification) are still ahead. Re-run `/awos:tutorial 002` once Slice 10 is verified to refresh against the full spec. The earlier v1 of this tutorial covered Slices 1–3 only; the substantive new ground here is **Slice 4** (real graph hosted, HITL across the wire) and the **Bedrock model-family switch from Anthropic Claude to Amazon Nova** per ADR 003.

---

## Overview

Spec 001 left Graphia as a single-process console program — `uv run python -m graphia` boots a LangGraph state graph in the developer's terminal, persists checkpoints to a local sqlite file, and calls Bedrock via the developer's credentials. Phase 2 (this increment) takes that same compiled graph and moves it into an AWS-managed compute surface: **Amazon Bedrock AgentCore Runtime**, a serverless container service in the Bedrock family designed for hosting agent workloads.

The interesting design problem this increment opens is: **how does a Python program become an AWS-managed container, while keeping the same LangGraph topology and the same `ChatBedrockConverse` clients that worked locally?** The central technology that answers it is **AgentCore Runtime** (the deployment target) combined with the `bedrock-agentcore` Python SDK (the in-process contract) and a Terraform module backed by `hashicorp/aws 6.44` (the IaC layer that ties them together). The tutorial teaches that stack from the core outward — the AgentCore deployment contract first, then the IAM/auth seam that lets the workload talk to Bedrock, then the Terraform posture, then the build-and-release loop, then the wire that now connects the local Textual UI to the deployed Runtime end-to-end.

What you can do at the end of Slice 4: run `make deploy` and end up with an AgentCore Runtime in `us-east-1` running the real compiled graph, then run `uv run python -m graphia --remote` and play a complete Mafia game against the deployed runtime — Night, Day, vote, end-of-game banner — with HITL inputs round-tripping over the wire. Local mode (`uv run python -m graphia` without `--remote`) still plays exactly as before. The graph the Runtime executes calls **Nova Pro** for gameplay and **Nova Lite** for roster generation (per ADR 003; spec 001's Claude / `us.*` profile path is documented but no longer active).

---

## Concepts already covered (referenced, not re-taught)

- **`env-config-via-dotenv-with-validation`** — `python-dotenv` loads `.env`; `GraphiaConfig` is the typed dataclass exposing values. (See [tutorial 001](../001-playable-skeleton/tutorial.md).) Spec 002 *extends* the dataclass with `remote_mode` and `runtime_invocation_url`.
- **`utf8-stream-reconfig-at-entry`** — `__main__.py` reconfigures stdio to UTF-8 before any imports. (See [tutorial 001](../001-playable-skeleton/tutorial.md).) Still runs first; spec 002 only adds an argparse step *after* it.
- **`chatbedrockconverse-singleton`** — Two LLM singletons configured once and reused. (See [tutorial 001](../001-playable-skeleton/tutorial.md).) The singletons survive; the model family swapped under them (Claude 4.5 → Nova Pro/Lite per ADR 003), but the singleton concept and the `with_structured_output(...)` discipline are unchanged.
- **`regional-inference-profile-prefix`** — Bedrock model IDs used the `us.anthropic.…` regional profile prefix. (See [tutorial 001](../001-playable-skeleton/tutorial.md).) **Retired in this increment per ADR 003** — Graphia now invokes Nova foundation models directly, with no inference profile. The concept stays as a historical record of what the playable skeleton genuinely did; the running code no longer uses it.

---

## What's new this increment

- [**Managed-container Runtime as the deployment target**](#the-agentcore-runtime-contract) — AgentCore Runtime is a scale-to-zero, `linux/arm64`-only managed container surface.
- [**Bedrock-AgentCore Python entrypoint**](#the-agentcore-runtime-contract) — A single decorated function becomes the agent's invocation contract.
- [**Explicit host binding for Podman compatibility**](#the-agentcore-runtime-contract) — `app.run(host="0.0.0.0")` bypasses the SDK's `/.dockerenv` heuristic.
- [**Async-generator entrypoint streams super-steps as SSE**](#the-agentcore-runtime-contract) — Yielding from an `async def` handler maps one yield to one SSE `data:` frame on the wire.
- [**Start vs resume payload contract**](#the-agentcore-runtime-contract) — Wire-level shape that distinguishes a fresh game from a resume of an existing one.
- [**Workload credentials via IAM execution role**](#bedrock-credentials-inside-a-managed-workload) — The Runtime assumes a role; `ChatBedrockConverse` finds it via boto3's default credential chain.
- [**Standard-credential-chain auth in `GraphiaConfig`**](#bedrock-credentials-inside-a-managed-workload) — Bearer-token demoted to legacy fallback; profile name never hardcoded in source.
- [**Nova direct on-demand in us-east-1**](#bedrock-credentials-inside-a-managed-workload) — Amazon Nova Pro + Nova Lite as the gameplay + roster singletons, invoked directly against the foundation-model ARN with no inference profile (ADR 003).
- [**AgentCore resources in the AWS provider**](#declaring-the-deployment-in-terraform) — `aws_bedrockagentcore_agent_runtime` (note `_agent_` infix); no `invocation_url` attribute; clients invoke via ARN.
- [**Required tags via provider `default_tags`**](#declaring-the-deployment-in-terraform) — One tag map; every taggable resource inherits.
- [**Single name prefix with regex-aware variants**](#declaring-the-deployment-in-terraform) — `local.name_prefix` drives all resource names; underscore variant for AgentCore Runtime.
- [**ECR force-delete safeguard**](#declaring-the-deployment-in-terraform) — Default-off; two-step override because the provider reads `force_delete` from prior state.
- [**Terraform run inside a pinned container**](#running-terraform-reproducibly) — `./tf` wrapper auto-detects Podman/Docker, uses `hashicorp/terraform:1.13.1`.
- [**Multi-stage uv-driven Dockerfile**](#the-build--push--apply-loop) — Cache-efficient layer ordering: dep install before source copy.
- [**Makefile as project-wide task-runner**](#the-build--push--apply-loop) — Orchestrates `./tf` + container runtime + AWS CLI; defaults from `git config` and `git rev-parse`.
- [**Image-driven Runtime deploys**](#the-build--push--apply-loop) — `var.image_tag` (= git SHA) is the handle AgentCore reads.
- [**Bootstrap-then-apply first deploy**](#the-build--push--apply-loop) — Targeted ECR apply → push → full apply solves the chicken-and-egg.
- [**boto3 invoke_agent_runtime is the client-side path**](#crossing-the-wire-hitl-with-the-deployed-runtime) — `boto3.client('bedrock-agentcore').invoke_agent_runtime` is what the local client uses to reach the deployed Runtime.
- [**Mode-agnostic consumer via shared chunk shape**](#crossing-the-wire-hitl-with-the-deployed-runtime) — Local and remote producers emit the same `{node_name: update}` chunks; the UI rendering path doesn't know which one's talking.
- [**Dual-mode config with contradiction check**](#connecting-local-to-remote-the-seam-wired) — `GraphiaConfig.remote_mode` + `runtime_invocation_url`; raises only on inconsistent state.
- [**Argparse → env → typed config bridge**](#connecting-local-to-remote-the-seam-wired) — CLI flag promotes via env var to the dataclass, preserving the dataclass as single source of truth.

---

## Diagram

```mermaid
flowchart LR
    subgraph local [Developer machine]
        TUI["Textual TUI<br/>uv run python -m graphia --remote"]
        Driver["Driver<br/>_consume_stream + _producer<br/>(branches on remote_mode)"]
        Config["GraphiaConfig<br/>+ remote_mode<br/>+ runtime_invocation_url"]
        Client["AgentCoreClient<br/>boto3 invoke_agent_runtime"]
        TUI --> Driver
        Driver --> Client
        TUI -.-> Config
    end

    subgraph aws ["AWS us-east-1: Slice 4 deliverable"]
        ECR[("ECR repo<br/>graphia-demo-runtime<br/>tag = git SHA")]
        Runtime["AgentCore Runtime<br/>graphia_demo_runtime-XXXX<br/>BedrockAgentCoreApp<br/>@app.entrypoint (async generator)<br/>real compiled graph"]
        Role["IAM execution role<br/>trust: bedrock-agentcore.amazonaws.com<br/>InvokeModel + ECR pull + Logs"]
        LG[("CloudWatch Log Group<br/>/aws/bedrock-agentcore/...<br/>30-day retention")]
        Bedrock["Bedrock<br/>amazon.nova-pro-v1:0<br/>amazon.nova-lite-v1:0"]
        ECR -- "image at create-time" --> Runtime
        Runtime -- "assumes" --> Role
        Role -- "InvokeModel" --> Bedrock
        Runtime -- "writes" --> LG
    end

    Client == "InvokeAgentRuntime<br/>SSE stream of super-steps" ==> Runtime

    style Runtime fill:#dde,stroke:#447
    style ECR fill:#efe,stroke:#474
    style Role fill:#fee,stroke:#744
    style Client fill:#eef,stroke:#447
```

---

## Walkthrough

### The AgentCore Runtime contract

**Pose.** How does a Python program become an AWS-managed container workload that other AWS services can invoke? Specifically — what's the *minimum* code change a developer writes to take an already-working agent and host it on AgentCore Runtime?

**Present.** **Bedrock AgentCore Runtime** is a managed container service in the Bedrock family. It takes a `linux/arm64` container image from ECR, runs it on Graviton infrastructure, fronts it with a service-managed HTTP endpoint, scales to zero between invocations, and routes inbound calls to an entry-point Python callable inside the container. The in-process contract is the **`bedrock-agentcore` SDK**: a `BedrockAgentCoreApp` instance, a function decorated with `@app.entrypoint`, and a single `app.run()` call. The SDK bundles Starlette + Uvicorn internally and hides them — no Flask, no FastAPI, no manual ASGI server wiring at the application layer.

For Graphia the entry-point needs more than a single-shot handler — it needs to *stream* the graph's super-steps back to the local client as they happen. That's what an **async-generator `@app.entrypoint`** gives us: the SDK detects an `async def … -> AsyncIterator[dict]` via `inspect.isasyncgenfunction` and wraps each `yield` as one SSE `data:` frame on the wire. One graph super-step yield → one event on the client.

**Apply.** The Runtime entry-point lives in `src/graphia/runtime/__main__.py`. The shape:

```python
# src/graphia/runtime/__main__.py — async-generator handler (excerpt)
@app.entrypoint
async def handler(payload: dict) -> AsyncIterator[dict]:
    parsed = _validate(payload)
    if isinstance(parsed, dict):
        yield parsed; return
    action, thread_id, body = parsed
    graph = build_runtime_graph(thread_id, _CHECKPOINT_DIR)
    run_config = {"configurable": {"thread_id": thread_id}}
    graph_payload = body if action == "start" else Command(resume=body)
    for chunk in graph.stream(graph_payload, run_config, stream_mode="updates"):
        for ev in _serialise_chunk(chunk):
            yield ev
    # ... trailing interrupt or done event
```

Several concepts compose here. **Managed-container Runtime as the deployment target** says the unit of deployment is a container image, and the platform is fixed: `linux/arm64`, Graviton. The Dockerfile (later section) and the build pipeline both treat that as a constant. **Bedrock-AgentCore Python entrypoint** says the SDK gives the developer one decorator and one `run()` call as the entire deployment contract. **Explicit host binding for Podman compatibility** is a small but load-bearing detail: the SDK auto-detects "am I in a container?" by checking for `/.dockerenv`, and binds to `127.0.0.1` if it can't find the marker file. Podman doesn't create `/.dockerenv` — so without `host="0.0.0.0"`, the container would bind to localhost and external probes (including AgentCore's own health-checks) would get "empty reply from server". Binding explicitly is runtime-agnostic and makes the network contract visible at the call site.

The **start vs resume payload contract** is what gives HITL its over-the-wire shape. A fresh game arrives as `{"action": "start", "thread_id": "<uuid>", "initial_state": {…}}`; a resume after the user enters their name arrives as `{"action": "resume", "thread_id": "<same uuid>", "resume_value": "Alice"}`. The handler dispatches on `action` and passes either the initial state or `Command(resume=value)` into `graph.stream(...)`. The same `thread_id` for start + every resume is what lets the server-side `SqliteSaver` (writing to the container's `/tmp/graphia/checkpoints/`) find the right checkpoint.

### Bedrock credentials inside a managed workload

**Pose.** Spec 001's **`chatbedrockconverse-singleton`** called Bedrock with the developer's credentials — a bearer token in `.env` for the workshop path, or a profile resolved via boto3's default chain on a developer machine. Inside a deployed AgentCore Runtime, *nobody types a token*. So: how does the same singleton get authenticated when it's not the developer's machine making the call?

**Present.** AgentCore Runtime workloads run under an **IAM execution role** that the service assumes for them. The role's trust principal is `bedrock-agentcore.amazonaws.com`; its inline policy lists exactly the actions the workload is allowed to perform — `bedrock:InvokeModel` (and the streaming variant) against the foundation-model ARNs the agent uses, ECR pull on the workload's own image, CloudWatch log write on the workload's own log group. When the container boots, the SDK exposes the role's credentials through the standard AWS environment-variable conventions, and `boto3` finds them via its **default credential chain** — the very same chain a developer's local `boto3` walks through `AWS_PROFILE`, SSO cache, instance metadata, etc. The `ChatBedrockConverse` singletons need *no code change* to work in either environment; the *credential source* is what differs.

The model family the singletons hold is **Amazon Nova** rather than spec 001's Anthropic Claude. ADR 003 documents the switch in full — the short version is that Claude 4.5 (and most current Claude variants) require an inference profile for on-demand invocation; the cross-region `us.*` system profile fanned out to regions where the role couldn't auto-subscribe via Marketplace. Nova Pro + Nova Lite support direct on-demand in us-east-1 with no profile, so the IAM scope collapses to one foundation-model ARN pattern in one region.

**Apply.** The Bedrock statement of the role's inline policy lives in `infra/terraform/main.tf` inside `data.aws_iam_policy_document.runtime_inline`:

```hcl
# infra/terraform/main.tf — data.aws_iam_policy_document.runtime_inline (BedrockModelInvoke statement)
statement {
  sid     = "BedrockModelInvoke"
  effect  = "Allow"
  actions = [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
  ]
  resources = local.bedrock_invoke_resources
}
```

`local.bedrock_invoke_resources` (in `infra/terraform/locals.tf`) is now a single ARN pattern: `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*`. **Nova direct on-demand in us-east-1** is the concept: no profile, no cross-region routing, least-privilege at the foundation-model ARN layer. **Workload credentials via IAM execution role** layers on top: the *role itself* is what binds workload identity to Bedrock authorisation.

Meanwhile, the local `GraphiaConfig` is updated to reflect the new auth posture. Inside `src/graphia/config.py`'s `load_config()`:

```python
# src/graphia/config.py — load_config (auth resolution + contradiction check)
bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or None
# ...
remote_mode = _env_truthy("GRAPHIA_REMOTE")
runtime_invocation_url = os.environ.get("GRAPHIA_RUNTIME_URL") or None

if remote_mode and not runtime_invocation_url:
    raise SystemExit(
        "Remote mode requested (--remote / GRAPHIA_REMOTE=1) but "
        "GRAPHIA_RUNTIME_URL is not set. ..."
    )
```

**Standard-credential-chain auth** says boto3's default chain is now the canonical path — bearer-token is kept as an *optional* legacy fallback (the field is `str | None`), no longer required, and the profile name is never hardcoded in source (it comes from `AWS_PROFILE` in the user's environment). The earlier `SystemExit("AWS_BEARER_TOKEN_BEDROCK is not set")` from spec 001 is gone.

### Declaring the deployment in Terraform

**Pose.** AgentCore Runtime, ECR repo, IAM role, IAM inline policy, CloudWatch log group — five AWS resources that have to come up together, in the right order, with consistent names and tags. How do we declare them reproducibly so a fresh contributor (or CI) can stand up an identical stack with one command?

**Present.** `hashicorp/aws` provider v6.18+ added native AgentCore support; this module pins v6.44.0 exactly. The Runtime resource is **`aws_bedrockagentcore_agent_runtime`** — note the `_agent_` infix in the Terraform name, even though the CloudFormation type is `AWS::BedrockAgentCore::Runtime` (no "Agent"). Same resource, different naming convention. The resource also exposes **no `invocation_url` attribute**; clients invoke via the `agentRuntimeArn` against the `InvokeAgentRuntime` data-plane API.

**Apply.** Four concepts manage cross-resource consistency. **Required tags via provider `default_tags`** lives in `main.tf`'s provider block:

```hcl
# infra/terraform/main.tf — provider "aws"
provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}
```

`local.common_tags` (in `locals.tf`) is the canonical four-tag set: `Project=Graphia`, `ManagedBy=Terraform`, `Environment=var.environment`, `Owner=var.owner`. Every taggable AWS resource in the module inherits these — no per-resource `tags = …` repetition, no drift where one resource forgets a tag, no casing inconsistency. Resources that can't accept tags (IAM inline policy attachments) silently skip.

**Single name prefix with regex-aware variants** handles the AgentCore Runtime name quirk. In `infra/terraform/locals.tf`:

```hcl
# infra/terraform/locals.tf — locals (naming helpers)
name_prefix  = substr("graphia-${var.environment}", 0, 80)
runtime_name = substr(replace("${local.name_prefix}_runtime", "-", "_"), 0, 48)
```

ECR, CloudWatch, and IAM all accept the `graphia-demo-runtime` form. AgentCore Runtime's control plane enforces `[a-zA-Z][a-zA-Z0-9_]{0,47}` — letters / digits / underscores, max 48 chars, no dashes. So `runtime_name` is derived by `s/-/_/g` then truncating. One prefix; tooling cross-correlates ECR/IAM names to the Runtime name by `s/-/_/g`. AgentCore appends a service-side 10-char suffix at create time (e.g. `graphia_demo_runtime-C3WHk2BtFS`).

**ECR force-delete safeguard** is the operational concept that keeps the developer from a footgun. `var.ecr_force_delete` defaults to `false`, so `terraform destroy` *refuses* to drop the ECR repo while it still contains images:

```hcl
# infra/terraform/main.tf — aws_ecr_repository.runtime (safeguard line)
force_delete = var.ecr_force_delete
```

To override (intentional teardown), the path is **two-step**: first a targeted apply with `-var ecr_force_delete=true` to flip the attribute in state, then the destroy. Why two-step? Because the AWS provider reads `force_delete` from *prior state* at destroy time, not from the current config or `-var` — a known Terraform/provider gotcha. The two-step procedure is documented in `infra/terraform/README.md`.

**AgentCore resources in the AWS provider** ties these together: with the resource quirks (`_agent_` infix; no `invocation_url`), the regex-aware name variant, and the consistent tags, the five resources go up cleanly under one `make deploy`.

### Running Terraform reproducibly

**Pose.** Even with a clean Terraform module, "works on my Terraform 1.12 but not on your 1.13" is a real failure mode — providers, syntax, and behaviour drift across point releases. How do we make sure every contributor (and CI) runs the same exact Terraform binary?

**Present.** The module ships a **`./tf` wrapper script** that detects which container runtime is installed (Podman first, Docker as fallback), pulls a pinned `hashicorp/terraform:1.13.1` image, mounts the project directory plus the developer's `~/.aws` SSO cache, forwards `AWS_PROFILE` / `AWS_REGION`, and `exec`s the user-supplied terraform command inside the container. Every `./tf init`, `./tf plan`, `./tf apply` runs against the *same* terraform + provider versions on every machine.

**Apply.** The wrapper sits at `infra/terraform/tf`. Its key choice is **runtime auto-detection** in the runtime-selection block:

```bash
# infra/terraform/tf — runtime selection block
if command -v podman >/dev/null 2>&1; then
    RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then
    RUNTIME=docker
else
    echo "error: install Podman or Docker" >&2; exit 1
fi
```

This pattern — never hardcode `podman` or `docker` in project source; auto-detect at invocation — recurs in the Makefile (next section). Both runtimes are first-class supported; the contributor's environment picks. **Terraform run inside a pinned container** says the Terraform binary itself is project-controlled: bumping versions is a one-line edit to the wrapper, not a coordinated upgrade across every contributor's machine.

### The build → push → apply loop

**Pose.** Python code lives in `src/graphia/`; AWS-managed compute pulls a container image from ECR. What's the loop that gets a code change from `git commit` into a running AgentCore Runtime — idempotently, observably, and discoverably?

**Present.** Four concepts compose the loop. **Multi-stage uv-driven Dockerfile** builds the image. **Makefile as project-wide task-runner** orchestrates the steps (build → ECR auth → push → terraform apply). **Image-driven Runtime deploys** gives Terraform a handle (the image tag) that AgentCore reads to decide when to roll. **Bootstrap-then-apply first deploy** resolves the chicken-and-egg of "the Runtime needs an image in ECR, but the ECR repo is itself Terraform-managed."

**Apply.** The Dockerfile (`Dockerfile` at the repo root) uses two stages to keep the runtime image lean:

```dockerfile
# Dockerfile — builder stage (uv-driven dep install)
FROM python:3.13-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
```

Layer ordering matters: `pyproject.toml` + `uv.lock` are copied *before* `src/`, so a source edit only invalidates the project-install layer, not the dep-install layer. Rebuilds after a code change finish in seconds. The final stage just copies the `.venv` and sets `CMD ["python", "-m", "graphia.runtime"]`. The whole image is ≈ 330 MB.

The Makefile (`Makefile` at the repo root) ties build, push, and Terraform together under named composite targets:

```makefile
# Makefile — workflow composites
deploy: tf-init tf-ecr-bootstrap push tf-apply
	@cd $(TF_DIR) && ./tf output runtime_invocation_url

redeploy: push tf-apply
	@echo "Redeploy complete with image tag $(TAG)."
```

This is the **Makefile as project-wide task-runner** concept. The Makefile orchestrates multiple tools — `./tf` for Terraform, `podman`/`docker` for image builds, `aws` for ECR login — and defaults pull from the developer's environment: `OWNER ?= $(shell git config user.email)`, `TAG ?= $(shell git rev-parse --short HEAD)`, `ENVIRONMENT ?= demo`. Contributors and CI invoke the same thing; `make help` lists every target. The same runtime-agnostic auto-detect (`CONTAINER ?= $(shell command -v podman …)`) shows up here too — both `./tf` and the Makefile honour the "Podman or Docker, never hardcoded" policy.

**Image-driven Runtime deploys** is the Terraform-side half. The AgentCore Runtime resource interpolates `container_uri = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"` — the *tag string* is what Terraform tracks. `make redeploy` passes `-var image_tag=$(git rev-parse --short HEAD)`, so every commit's image is identifiable in CloudWatch logs by its git SHA, and bumping the tag is what tells AgentCore to roll the deployment. Pushing the same SHA twice with the same `image_tag` is a no-op from Terraform's perspective.

The **bootstrap-then-apply first deploy** resolves the chicken-and-egg: AgentCore Runtime validates the image at create-time, so the image must already be in ECR. But the ECR repo is itself a Terraform-managed resource. The first deploy runs as `tf-init → tf-ecr-bootstrap → push → tf-apply` — the `tf-ecr-bootstrap` step is a targeted apply (`./tf apply -target=aws_ecr_repository.runtime`) that creates only the repo, leaving the Runtime resource for the final step. Subsequent deploys collapse to `push + tf-apply` (`make redeploy`).

### Crossing the wire: HITL with the deployed Runtime

**Pose.** The Runtime hosts the real graph; the local Textual UI runs in a separate process on the developer's machine. The graph has `interrupt()` calls inside it that need human input. How does the local UI invoke a graph that lives in AWS, receive its super-steps live, prompt the human at interrupt boundaries, and send the human's response back to the *same* paused graph instance?

**Present.** Two concepts cooperate. **`boto3.client('bedrock-agentcore').invoke_agent_runtime`** is the client-side path — the `bedrock-agentcore` Python SDK ships server-side primitives only, so the local app reaches the deployed Runtime through boto3's data-plane API. **Mode-agnostic consumer via shared chunk shape** is what makes the rest of the codebase indifferent to which side of the wire the graph runs on: both the local `graph.stream(...)` and the remote `client.stream(...)` yield `{node_name: update}` dicts, including `{"__interrupt__": (Interrupt(value, id),)}` on a paused graph. The driver's `_consume_stream` reads chunks off an `asyncio.Queue` regardless of which producer filled it; the UI rendering pipeline doesn't know which one's talking.

**Apply.** The driver's producer-side switch lives in `src/graphia/driver.py`:

```python
# src/graphia/driver.py — _make_stream_iterator
def _make_stream_iterator(graph, client, payload, run_config) -> Iterator[dict]:
    if client is not None:
        return client.stream(payload, run_config, stream_mode="updates")
    return graph.stream(payload, run_config, stream_mode="updates")
```

The `AgentCoreClient` constructor (built once per `drive_graph` invocation) wraps a `boto3` client and exposes the same `stream(...)` signature `graph.stream(...)` has. Internally `AgentCoreClient` calls `invoke_agent_runtime`, parses the resulting SSE stream, and yields chunks in the local-mode shape. The HITL round-trip is unchanged from local mode — when the driver receives an `__interrupt__` chunk, it opens the right Textual modal, collects the human's response, and re-invokes the same `client.stream(...)` with a `Command(resume=value)` payload. Because the client passes a stable `runtimeSessionId` (derived from the `thread_id`), the resume lands on the same microVM the start did, and the server-side `SqliteSaver` finds the right checkpoint.

### Connecting local to remote: the seam, wired

**Pose.** With the producer-side wire in place, how does the local app *choose* between local and remote mode? The user types `--remote` at the command line, the typed `GraphiaConfig` exposes the choice to the rest of the codebase, and one branch on `config.remote_mode` decides whether the driver constructs an `AgentCoreClient` or not.

**Present.** Two concepts cooperate. **Argparse → env → typed config bridge** routes `--remote` through `os.environ["GRAPHIA_REMOTE"] = "1"` *before* `GraphiaConfig.load_config()` is called, so the typed dataclass remains the single source of truth — no parallel "is the flag set?" check spreads through the codebase. **Dual-mode config with contradiction check** gives the dataclass two new fields (`remote_mode`, `runtime_invocation_url`) and one invariant: raise only when the state is genuinely contradictory.

**Apply.** The argparse bridge sits in `src/graphia/__main__.py`'s `__main__` block:

```python
# src/graphia/__main__.py — __main__ block
if __name__ == "__main__":
    args = _parse_args()
    if args.remote:
        os.environ["GRAPHIA_REMOTE"] = "1"
    GraphiaApp().run()
```

The flag is parsed, promoted to an environment variable, and `GraphiaApp().run()` proceeds normally. Inside `GraphiaApp`, the very first thing that happens is `load_config()` — which reads `GRAPHIA_REMOTE` and `GRAPHIA_RUNTIME_URL` and either succeeds with `remote_mode=True` or raises a clear error pointing the user at `terraform output runtime_invocation_url`. Local mode (no flag) is unchanged; the dataclass `remote_mode` field defaults to `False` and the driver never constructs an `AgentCoreClient`.

The seam that v1 of this tutorial called "partial" is now wired: typing `--remote` flips one boolean in the config, the driver constructs an `AgentCoreClient` from `config.runtime_invocation_url` + `config.aws_region`, and every chunk the game emits comes from AWS rather than the local process.

---

## Try it

```bash
# 0. Authenticate.
aws sso login --profile my-aws-profile
export AWS_PROFILE=my-aws-profile

# 1. First-time deploy. Builds the image, bootstraps ECR, pushes, then applies.
make deploy

# 2. Inspect what landed.
cd infra/terraform && ./tf output && cd ../..
# runtime_invocation_url = "arn:aws:bedrock-agentcore:us-east-1:<acct>:runtime/graphia_demo_runtime-XXXXXXX"
# ecr_image_uri          = "<acct>.dkr.ecr.us-east-1.amazonaws.com/graphia-demo-runtime:<git-sha>"
# cloudwatch_log_group   = "/aws/bedrock-agentcore/graphia-demo-runtime"

# 3. Play a full game against the deployed Runtime.
export GRAPHIA_RUNTIME_URL=$(cd infra/terraform && ./tf output -raw runtime_invocation_url)
uv run python -m graphia --remote

# 4. Local mode still works unchanged.
uv run python -m graphia

# 5. Tear down when you're done (default-safeguarded; see infra/terraform/README.md for force-delete two-step).
make destroy
```

What "working" looks like:

- `./tf output runtime_invocation_url` returns an ARN with the AgentCore-generated 10-character suffix (e.g. `graphia_demo_runtime-C3WHk2BtFS`).
- The AWS console (Bedrock → AgentCore → Runtimes) shows the runtime as `ACTIVE`.
- The image in ECR has a tag equal to `git rev-parse --short HEAD`.
- CloudWatch has a log group `/aws/bedrock-agentcore/graphia-demo-runtime` with `retention_in_days = 30`.
- `uv run python -m graphia --remote` opens the Textual UI; the name modal appears; entering your name advances to Night 1; the game plays Night → Day → Night to a decisive end-of-game banner.
- `uv run python -m graphia` (no `--remote`) plays a full game identically to spec 001.

A code-cycle redeploy is `make redeploy`: it builds the new image with the new git SHA, pushes, and applies — the Runtime rolls because `var.image_tag` changed.

---

## Where to go next

- Next tutorial: **003 (TBD)** — will cover Slice 5+ (corner badge, AgentCore Memory, Gateway, Observability + failure modal, equivalence tests, destroy verification) once spec 002 is verified.
- Related ADRs:
  - [ADR 001 — Hosted AgentCore Runtime with Preserved Local Mode](../../adr/001-hosted-agentcore-with-local-mode.md) — *why* both modes are first-class.
  - [ADR 002 — Runtime-Embedded Gateway Tool Handlers](../../adr/002-runtime-embedded-gateway-tool-handlers.md) — *why* Slice 7's Gateway sits in front of the Runtime rather than separate Lambdas.
  - [ADR 003 — Bedrock Model Family: Amazon Nova over Anthropic Claude](../../adr/003-bedrock-nova-over-claude.md) — *why* the gameplay + roster singletons hold Nova rather than Claude.
- Related CRs:
  - [CR 001 — AgentCore + tools in scope](../../change-requests/001-agentcore-and-tools-in-scope.md) — the upstream scope shift that brought AgentCore into v1.x.
  - [CR 002 — Long-term Memory in scope](../../change-requests/002-long-term-memory-in-scope.md) — Phase 6's Memory work.
- Pre-spec research: [`infrastructure-research.md`](../../spec/002-hosted-agentcore-deployment/infrastructure-research.md) — the AWS account survey + Terraform-coverage findings that landed before the tech spec.
