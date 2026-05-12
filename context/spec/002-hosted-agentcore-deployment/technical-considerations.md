# Technical Specification: Hosted AgentCore Deployment

- **Functional Specification:** [`context/spec/002-hosted-agentcore-deployment/functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Poe (on behalf of the project owner)

---

## 1. High-Level Technical Approach

Phase 2 ships **two parallel run modes** behind the same LangGraph topology: the existing local-only mode (unchanged behaviourally from spec 001) plus a new **remote** mode in which the compiled `StateGraph` *runs inside* a Bedrock AgentCore Runtime workload deployed to `us-east-1`. The local Textual UI is the same in both modes; in remote mode it acts as an AgentCore *client* that invokes the deployed Runtime, streams super-step events back through the existing message-rendering pipeline, and round-trips `interrupt()` / `Command(resume=…)` for the human's turns. Two parallel implementations of a new `DiaryStore` interface sit behind the same gameplay code: an in-process implementation for local mode, and an AgentCore-Memory-backed implementation (one record per diary entry) for remote mode. The remote-mode Memory access is routed through an AgentCore Gateway-fronted MCP surface whose handlers are *Runtime-embedded* — i.e., the same containerised Runtime workload exposes both the agent invocation API and a small HTTP surface that the Gateway publishes as MCP tools.

Infrastructure is provisioned via a Terraform module (`infra/terraform/`) that stands up the Runtime, the Gateway, the Memory store, and CloudWatch observability + 30-day log retention. The module relies on the standard AWS credential chain — region and account are configurable inputs with the project's documented defaults (`us-east-1`, account `123456789012`, SSO profile `my-aws-profile`); no profile name is hardcoded in source. The legacy `AWS_BEARER_TOKEN_BEDROCK` bearer-token auth path is demoted to an optional fallback; `GraphiaConfig` is refactored to make it optional and surface a typed run-mode field.

Three of Graphia's existing concerns continue to work unchanged in both modes: `add_messages` accumulating the message log, the streaming-updates iteration model, and the `interrupt()` / `Command(resume=…)` HITL pattern. The Runtime's session model (microVM per session, up to 8h, bidirectional streaming) accommodates Phase 2's single-session-per-game scope without needing a durable cross-session checkpointer; the existing per-thread `SqliteSaver` is reused, writing to the Runtime's session-local filesystem in remote mode.

---

## 2. Proposed Solution & Implementation Plan

### 2.1 Run-mode configuration

`GraphiaConfig` (`src/graphia/config.py`) is refactored:

| Field | Before | After |
|---|---|---|
| `bearer_token: str` (required) | Raised `SystemExit` if `AWS_BEARER_TOKEN_BEDROCK` missing | `bearer_token: str \| None`; `None` means "boto3 default credential chain" |
| `aws_region: str` (default `eu-north-1`) | n/a | `aws_region: str` (default `us-east-1`) |
| *(new)* `remote_mode: bool` | — | `True` when `--remote` flag set, else `False` |
| *(new)* `runtime_invocation_url: str \| None` | — | The deployed AgentCore Runtime's invocation endpoint (None in local mode); read from `GRAPHIA_RUNTIME_URL` env, populated by `terraform apply` output that the developer copies into `.env` (or written by a small `terraform output | jq …` helper documented in the README) |
| *(unchanged)* `log_file: Path`, `seed: int`, `checkpoint_dir: Path` | | |

Auth precedence in `load_config()`:
1. If `AWS_BEARER_TOKEN_BEDROCK` is set, use it (legacy / workshop-token path).
2. Otherwise, leave `bearer_token=None` and rely on boto3's default credential chain (`AWS_PROFILE`, instance role, etc.). This is the canonical 2026-05-12 posture from the `project_aws_account` memory.
3. `load_config()` does **not** raise on missing bearer-token any more — only on a *contradictory* config (e.g., `--remote` set but `GRAPHIA_RUNTIME_URL` empty).

### 2.2 Entry-points

- **`src/graphia/__main__.py`** parses `--remote` from `sys.argv` (argparse), sets `GRAPHIA_REMOTE=1` so the rest of the codebase reads it through `GraphiaConfig.remote_mode`, then dispatches to the Textual app as before.
- **`src/graphia/runtime/__main__.py`** (new) is the entry-point baked into the AgentCore Runtime container image. It builds the compiled graph (without a Textual UI), wraps it with `AgentCoreApp(graph)`, and calls `.serve()`. See §2.5.

### 2.3 Driver: branching on remote mode

`src/graphia/driver.py`'s `drive_graph(…)` function fans out by mode:

- **Local mode** (existing behaviour): the existing producer-thread + `asyncio.Queue` pattern runs `graph.stream(payload, run_config, stream_mode="updates")` on the locally-compiled graph. Unchanged from spec 001.
- **Remote mode**: a new sibling `_remote_producer` function uses the `bedrock_agentcore` SDK's client to invoke the deployed Runtime's streaming endpoint. The Runtime emits the same `{node_name: update}` chunk shape; the consumer side (`_consume_stream`) is identical because it consumes from the `asyncio.Queue` regardless of which producer filled it. The HITL round-trip is handled by the existing `request_resume(...)` callback: when the Runtime pauses on `interrupt()`, the streamed event carries the interrupt payload; the local client opens the right modal; the user's response is sent back via the SDK's `Command(resume=value)` re-invocation against the same `thread_id`.

This branching is the only mode-aware site outside of `config.py` and the `DiaryStore` abstraction.

### 2.4 `DiaryStore` abstraction

A new `src/graphia/diary_store.py` defines:

```python
# (Shape, not full impl — implementation lives in the file itself.)
class DiaryStore(Protocol):
    def write(self, game_id: str, player_id: str, night_index: int, content: str) -> None: ...
    def read(self, game_id: str, player_id: str) -> list[DiaryEntry]: ...
```

Two implementations:

- **`InProcessDiaryStore`** — backed by `PlayerState.diary_entries` in `GameState`. Used in local mode. No external state.
- **`AgentCoreMemoryDiaryStore`** — wraps the AgentCore Memory SDK (`bedrock_agentcore.memory.AgentCoreMemory`). Used in remote mode. Stores one record per diary entry (see §2.6); `read(game, player)` performs a filtered `search` and returns matching entries sorted by `night_index`.

Both implementations are selected at runtime in the graph's setup phase by a small factory that reads `GraphiaConfig.remote_mode`. The selected `DiaryStore` is injected into the relevant graph nodes (currently a Phase-6 concern, but the seam is created in Phase 2 with placeholder diary writes — see spec 002 §2.4).

### 2.5 AgentCore Runtime application-side

The Runtime workload is a containerised Python application packaged from `src/graphia/`. The entry-point (`src/graphia/runtime/__main__.py`) does three things:

1. **Build the compiled `StateGraph`** the same way the local app does, but configured for remote-mode persistence — the `SqliteSaver` writes to a Runtime-local path (e.g., `/tmp/graphia/checkpoints/`). Sessions are ephemeral (single-game scope); we accept that a crash mid-game cannot be resumed in a new session.
2. **Wrap the compiled graph** in `BedrockAgentCoreApp` from `bedrock_agentcore` (top-level re-export of `bedrock_agentcore.runtime.app.BedrockAgentCoreApp`). Register the handler via `@app.entrypoint` and start the server with `app.run(host="0.0.0.0")`. The Runtime invokes this app for each session.

   The explicit `host="0.0.0.0"` is load-bearing: the SDK auto-detects "am I in a container?" by looking for `/.dockerenv` or `DOCKER_CONTAINER=1`, and binds to `127.0.0.1` otherwise. Podman doesn't create `/.dockerenv`, so without the explicit host the container would bind to localhost and external probes (including AgentCore's health-check) get "empty reply from server". Binding to `0.0.0.0` works in every container runtime and removes the implicit dependency on Docker-specific filesystem heuristics.
3. **Expose a small HTTP surface** alongside the agent invocation (still inside the same container) that publishes the diary read/write endpoints (`POST /tools/diary/write`, `POST /tools/diary/read`). The Gateway registers these as `api`-type MCP targets — see §2.7. This means the *same* container handles both agent execution and tool execution; Gateway's role becomes routing + MCP envelope + Cedar-policy plane (Cedar is out-of-scope for v1.x per CR 001 amendment, but Gateway's MCP envelope and centralised auditing remain).

The Runtime's IAM execution role is provisioned by the Terraform module and has permissions for: Bedrock model invocation (the agent makes LLM calls), AgentCore Memory read/write, and CloudWatch logs write. The Runtime makes Bedrock calls via the boto3 default chain — `ChatBedrockConverse` is constructed with no explicit profile, and boto3 finds the execution role's credentials automatically.

### 2.6 AgentCore Memory record schema (one record per diary entry)

Per the design decision in this turn:

- **Per-record content:**
  ```
  {
    "kind": "diary_entry",
    "game_id": "<game thread_id>",
    "player_id": "<player uuid>",
    "night_index": <int>,
    "content": "<placeholder text or, in Phase 6, the AI's real diary>"
  }
  ```
- **Agent identity:** the AgentCore Memory store is scoped to a single `agent_id` per Runtime workload (i.e., "graphia-mafia-agent"); all per-game records co-exist under that id, distinguished by `game_id` in the record body.
- **Reads** use `AgentCoreMemory.search(query=...)` with a filter expression matching the `(game_id, player_id)` pair, then sort by `night_index` client-side. (If the SDK's `search` proves too fuzzy for exact-match filtering, an alternative is `list_memories` with a metadata filter; the terraform-aws / langgraph-agentic agents will confirm the exact API shape during `/awos:tasks`.)
- **Writes** are append-only via `AgentCoreMemory.store({...})`. No update-in-place; each Night appends a fresh record per surviving AI player.
- **End-of-game** leaves records in the store; the spec acknowledges this (§2.4 acceptance criterion 4).
- **Cleanup** on `terraform destroy` follows the Memory resource's natural lifecycle: destroying the Memory resource drops its stored records. This is the assumed behaviour; the spec's §2.7 acceptance criterion treats it as binding. To be validated against AWS docs (`aws-knowledge-mcp-server`) during `/awos:tasks` before the criterion is committed.

### 2.7 AgentCore Gateway tool registration (Runtime-embedded handlers)

The Gateway is provisioned by Terraform with two MCP tool targets, both pointing back at the Runtime's HTTP surface (§2.5):

| Tool name | Target type | Path | Description |
|---|---|---|---|
| `diary_write` | `api` (Runtime-embedded) | `POST /tools/diary/write` | Writes one diary entry for `(game_id, player_id, night_index)`. |
| `diary_read` | `api` (Runtime-embedded) | `POST /tools/diary/read` | Reads all diary entries for `(game_id, player_id)`, sorted by `night_index`. |

The agent inside the Runtime discovers these tools through the Gateway's MCP endpoint (`bedrock_agentcore.gateway.discover()` or equivalent). Each tool invocation flows: agent → Gateway MCP → back to the Runtime's HTTP endpoint → underlying `AgentCoreMemoryDiaryStore` call. The round-trip through Gateway is the v1.x Gateway demonstration (centralised audit point, MCP envelope, policy-enforcement seam for future Cedar work).

### 2.8 Local client → Runtime invocation flow

When `uv run python -m graphia --remote`:

1. The Textual app boots normally and renders the `[remote]` corner badge instead of `[local]` (§2.10).
2. The driver constructs an AgentCore client (`bedrock_agentcore.client.RuntimeClient`) from `config.runtime_invocation_url` + the boto3 default credential chain.
3. `drive_graph(...)` calls the client's streaming invocation API with `payload=initial_state`. The Runtime begins iterating super-steps; each chunk streams back to the client and lands on the same `asyncio.Queue` that local mode populates from its producer thread.
4. On `interrupt()` inside the Runtime, the streamed event includes the interrupt payload (`{"kind": "name"}`, etc.). The local UI dispatches to the right modal (using the existing `interrupt-payload-dispatch-to-modal` pattern from spec 001), collects the user's response, and re-invokes the client with `Command(resume=<value>)`. The Runtime resumes the same session (same `thread_id`).
5. When the graph reaches `END`, the Runtime's session terminates; the client closes the stream and the Textual app shows the end-of-game screen.

### 2.9 Terraform module structure

`infra/terraform/` contains the standard layout per the `terraform-conventions` skill installed via `/awos:hire`:

```
infra/terraform/
├── versions.tf      # pinned terraform >= 1.13.0 + hashicorp/aws = 6.44.0
├── main.tf          # AgentCore resources + IAM + CW log group + ECR
├── variables.tf     # region, account_id, environment, owner, agent_id, image_tag, ecr_force_delete
├── outputs.tf       # runtime ARN, log group, ECR repo URL + image URI, Memory namespace
├── locals.tf        # tag map, computed names (name_prefix + runtime_name variants)
├── README.md        # Makefile-driven apply / destroy procedures
├── RESEARCH.md      # Registry + AWS-docs cross-checks for AgentCore resources
└── tf               # Container-runtime wrapper script (runs hashicorp/terraform:1.13.1)
```

**No `profile` variable** — the standard AWS credential chain (e.g., `AWS_PROFILE` resolving an SSO profile) is the only auth path. Per the `project_aws_account` memory, the profile name is never hardcoded in source.

Resource names confirmed against the Registry + AWS docs (see RESEARCH.md §6–§7):

- **`aws_bedrockagentcore_agent_runtime`** — the containerised Runtime. Note the `_agent_` infix in the resource name (CloudFormation calls the same thing `AWS::BedrockAgentCore::Runtime`, without "Agent"; same resource, different naming convention). Pointed at the published ECR image, with `network_mode = "PUBLIC"` and the IAM execution role attached. **The resource exposes no `invocation_url` attribute**; clients invoke via `InvokeAgentRuntime` against the ARN.
- `aws_bedrockagentcore_gateway` + `aws_bedrockagentcore_gateway_target` × 2 — the two MCP tool targets pointing back at the Runtime's `/tools/diary/*` endpoints (Slice 7).
- `aws_bedrockagentcore_memory` — the Memory store keyed by `agent_id` (Slice 6).
- `aws_cloudwatch_log_group` with `retention_in_days = 30` — provisioned in **Slice 3** (not deferred to Slice 8) because the Runtime needs a write destination from boot. Slice 8 layers metric filters + the Textual failure modal on top.
- IAM execution role for the Runtime (`AssumeRolePolicyDocument` allowing `bedrock-agentcore.amazonaws.com`); inline policy for Bedrock invoke + AgentCore Memory R/W + CloudWatch logs write.
- ECR repository for the Runtime image; image push is an `apply`-prerequisite (see §2.12 below).

#### Resource naming convention

All resources derive their name from `local.name_prefix = "graphia-${var.environment}"` (capped at 80 chars). For `var.environment = demo`:

| Resource | Name | Notes |
|---|---|---|
| `aws_ecr_repository.runtime` | `graphia-demo-runtime` | |
| `aws_cloudwatch_log_group.runtime` | `/aws/bedrock-agentcore/graphia-demo-runtime` | |
| `aws_iam_role.runtime` | `graphia-demo-runtime` | Same bare name as the ECR repo — disambiguated by ARN-type namespacing. |
| `aws_iam_role_policy.runtime` | `graphia-demo-runtime-inline` | |
| `aws_bedrockagentcore_agent_runtime.this` | `graphia_demo_runtime` | **Underscores**, max 48 chars (control-plane regex `[a-zA-Z][a-zA-Z0-9_]{0,47}` — dashes forbidden). Service appends a 10-char suffix at create time (e.g. `graphia_demo_runtime-C3WHk2BtFS`). |

`locals.runtime_name` derives the underscore variant by `replace(local.name_prefix, "-", "_") + "_runtime"`, truncated to 48 chars. Tooling that cross-correlates ECR/IAM names with the Runtime name applies `s/-/_/g`.

#### ECR `force_delete` safeguard

The ECR resource's `force_delete` attribute is wired to `var.ecr_force_delete` (default `false`). Default-off prevents accidental image purge on routine `terraform destroy` invocations — a familiar foot-gun where a `make destroy` after a long demo session would wipe months of pushed image history.

To override (Slice 10 cleanup verification + dev cycle resets), the override path is **two-step** because the AWS provider reads `force_delete` from prior state at destroy time (not from the current config or `-var`):

```bash
# 1. Targeted apply flips force_delete: false → true in state.
./tf apply -target=aws_ecr_repository.runtime \
           -var environment=demo -var owner=$(git config user.email) \
           -var ecr_force_delete=true

# 2. Destroy reads true from state and sends Force=true.
make tf-destroy ECR_FORCE_DELETE=true
```

#### Default tags (applied via the provider's `default_tags` block)

| Tag | Value | Source |
|---|---|---|
| `Project` | `Graphia` | constant in `local.common_tags` |
| `ManagedBy` | `Terraform` | constant in `local.common_tags` |
| `Environment` | `<env>` | `var.environment` (required input) |
| `Owner` | `<email>` | `var.owner` (required input; Makefile defaults to `git config user.email`) |

PascalCase casing matches the org's de-facto canonical (per `infrastructure-research.md` §1.3 — multiple casings coexist in the account; PascalCase is the dominant form to pick).

### 2.10 UI changes (lighter — Textual is decorative)

Two small additions to `src/graphia/ui/`:

- A `CornerBadge` widget that shows `[local]` or `[remote]` based on `GraphiaConfig.remote_mode`. CSS positions it absolutely in a corner, doesn't intercept input, doesn't redraw on game events.
- A failure modal that surfaces a CloudWatch log-group link + filter expression for the failed session id, when remote-mode play crashes (per spec 002 §2.5 acceptance criterion 3).

### 2.11 Observability wiring

Per the design decision in this turn:

- **AgentCore Observability** (enabled on the Runtime resource via Terraform) emits structured traces to the CloudWatch log group whose name is exposed as a Terraform output. The log group is provisioned in **Slice 3** (alongside the Runtime + IAM role), not deferred to Slice 8 — the Runtime needs a write destination from its first boot. Slice 8 adds the *agent-side* trace-event emission with `thread_id` correlation and the Textual failure modal that surfaces a log-group filter when a remote game crashes. Trace events include Runtime session lifecycle, Gateway tool invocations, Memory read/write operations, and agent decision steps.
- **Bedrock model-invocation tracing** is *not* custom-instrumented in Phase 2. Model-call boundaries are visible in the AgentCore agent-decision traces (you see the agent invoking the model and the response coming back); full prompt/response capture is available via an opt-in LangSmith path if `LANGSMITH_API_KEY` is set in the Runtime's environment. The codebase does not assume LangSmith's presence (existing convention from spec 001).
- **Local-mode** observability is unchanged: JSONL trace to `GRAPHIA_LOG_FILE` only; no CloudWatch.
- **30-day CloudWatch retention** is set explicitly via the `aws_cloudwatch_log_group` resource (`retention_in_days = 30`).

### 2.12 Deployment workflow (Makefile task-runner)

The repo-root `Makefile` is the project's task-runner: it orchestrates the underlying tools — `./tf` (Terraform-in-container; see §2.9), `podman` / `docker` (image build, runtime-agnostic per the `project_terraform_container` memory), and the AWS CLI (ECR auth) — into named workflows. READMEs and tasks.md reference `make <target>`; raw multi-step shell sequences are not the contract. `make help` is the discoverable surface.

#### Canonical targets

| Target | Phase | What it runs |
|---|---|---|
| `make build` | dev | Build the runtime container image for `linux/arm64` (AgentCore Runtime contract); tag `:<git_sha>` and `:latest`. |
| `make run` | dev | Build then run the container locally on port 8080. |
| `make push` | dev | Build → ECR login → tag → push both `:<git_sha>` and `:latest` to the ECR repo. |
| `make tf-plan` / `tf-apply` / `tf-destroy` | IaC | Inspection / apply / destroy with `environment`, `owner`, `image_tag` plumbed automatically. |
| `make deploy` | first-time | `tf-init → tf-ecr-bootstrap → push → tf-apply`. |
| `make redeploy` | steady-state | `push → tf-apply` with the current SHA as `image_tag`. |
| `make destroy` | teardown | Alias for `tf-destroy`. Default-safeguarded against ECR purge (see §2.9). |

#### Default values (overridable via `make <target> FOO=bar`)

| Variable | Default | Comes from |
|---|---|---|
| `TAG` | `$(git rev-parse --short HEAD)` | local git state |
| `OWNER` | `$(git config user.email)` | local git config |
| `ENVIRONMENT` | `demo` | project default |
| `PLATFORM` | `linux/arm64` | AgentCore Runtime requirement |
| `AWS_REGION` | `us-east-1` | `project_aws_region` memory |
| `AWS_ACCOUNT` | `123456789012` | `project_aws_account` memory |
| `CONTAINER` | auto-detect `podman` then `docker` | `project_terraform_container` memory |
| `ECR_FORCE_DELETE` | `false` | safeguard against accidental image purge on destroy |

#### Image-driven deploys

`var.image_tag` is what makes the AgentCore Runtime roll: the resource's `container_uri` interpolates `"${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"`, so changing the tag is the change Terraform sees. Pushing the same git SHA twice without bumping the tag is a no-op for the Runtime resource even if ECR's image digest changed. `make redeploy` always passes `-var image_tag=$(git rev-parse --short HEAD)`, so each commit's image is identifiable in CloudWatch logs by its git SHA.

#### Bootstrap-then-apply (chicken-and-egg)

Creating an `aws_bedrockagentcore_agent_runtime` resource requires the ECR image to already exist — the control-plane API pulls the image at create-time and reports a failure if the tag is absent. But the ECR repo is itself a Terraform-managed resource. The first deploy resolves this by:

1. `tf-init` — initialise the module.
2. `tf-ecr-bootstrap` — `./tf apply -target=aws_ecr_repository.runtime`, which provisions *only* the ECR repo (4 other resources skipped via `-target`).
3. `push` — build and push the image to the now-existing ECR.
4. `tf-apply` — full apply with all 5 resources; the Runtime can now pull the image at create-time.

Subsequent deploys collapse to `push + tf-apply` (`make redeploy`) — ECR + log group + IAM role are already in state and the apply diff is image-tag-only.

### 3.1 System Dependencies

- **AWS provider Terraform resources for AgentCore.** The exact resource names + required arguments will be confirmed during `/awos:tasks` via `terraform-mcp-server`. If the AgentCore Runtime/Gateway/Memory resources are still under preview in `hashicorp/aws`, fallback options are: (a) `awscc` provider (Cloud Control), (b) a thin `null_resource` + `local-exec` wrapping the AgentCore CLI for the unsupported pieces. This fallback is implementation-time, not spec-time.
- **AgentCore SDK** (`bedrock-agentcore` Python package) on PyPI. Adds as a `uv add bedrock-agentcore` dependency. Used only by the runtime entry-point and the local AgentCore client.
- **AgentCore Memory `search` filter semantics.** The skill examples show a free-text `search(query=...)` API; the spec's exact-match `(game_id, player_id)` lookup may need a metadata-filter API instead. Validate at task time; fallback is client-side filtering on a broader fetch (acceptable for v1.x record counts).
- **AgentCore Runtime image registry.** Runtime workloads are deployed as container images; an ECR repo is provisioned by Terraform. Building + pushing the image is a prerequisite step before `terraform apply` succeeds; the README documents the one-liner.
- **`langgraph-agentcore` skill assumptions.** Several patterns above (the `AgentCoreApp(graph).serve()` wrapper, the `bedrock_agentcore.memory.AgentCoreMemory` API, the Gateway `add_target` shape) come from the skill's reference material. These should match the live AgentCore SDK; deltas surface during implementation.

### 3.2 Potential Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| AgentCore Memory `search` doesn't support exact-match filters tight enough for the diary-read pattern. | Medium | Fall back to client-side filtering on a broader `list_memories` fetch; acceptable for v1.x where each game has ≤30 entries per player. |
| Runtime container cold-start latency violates the spec's "same order of magnitude as local" feel. | Medium | AgentCore Runtime is scale-to-zero by default but cold-start is typically <2s. If latency proves disruptive, the spec's soft latency goal is qualitative and can be reconfirmed against measured numbers post-implementation. |
| Terraform AgentCore resource support is incomplete in `hashicorp/aws`. | High (early-2026 service) | Use `awscc` provider for the unsupported resources, or `null_resource` + AgentCore CLI as a temporary bridge. Investigation is the first task in `/awos:tasks`. |
| HITL round-trip across the wire doesn't survive Runtime-session timeouts during a long human pause (Day chat verbosity, coffee breaks). | Low for v1.x (8h session window > expected human-think time) | If it surfaces in testing, escalate to a durable cross-session checkpointer (Phase 6 concern per ADR 001 §5 implications). |
| Two parallel `DiaryStore` impls drift out of sync semantically. | Medium (real bug class per ADR 001) | Equivalence tests in `tests/` exercise both impls against the same scenarios. Mark as a required Phase 2 deliverable. |
| `AWS_BEARER_TOKEN_BEDROCK` field hold-over confuses developers who try to use it with `--remote` (it doesn't work for AgentCore APIs). | Medium | `GraphiaConfig.load_config()` validates: if `--remote` is set and only a bearer token is configured (no profile / no role), raise a clear error pointing the developer at `aws sso login`. |
| ECR image push gets out of sync with the Terraform-tracked image tag, leading to "deployed Runtime runs an old version" surprises. | Medium | Tag images by git SHA; surface the running image SHA in the AgentCore Runtime trace output so the developer can confirm what's actually running. Make `terraform apply` no-op if image hasn't changed. |

---

## 4. Testing Strategy

### 4.1 Unit tests

- **`tests/test_diary_store.py`** (new): exercises `InProcessDiaryStore` against an in-memory `GameState`. Verifies write/read shape, ordering by `night_index`, isolation between `(game_id, player_id)` pairs. The same test suite, parameterised, runs against a *mocked* `AgentCoreMemoryDiaryStore` (the AgentCore SDK is patched at the boundary like `safe_llm` patches `ChatBedrockConverse`). This is the equivalence-test surface the risk table calls out.
- **`tests/test_config_auth.py`** (new): exercises `load_config()` across the three auth posture combinations (bearer-only, profile-only via env, both). Validates the contradictory-config detection.
- **Existing slice tests** (`tests/test_slice2_roster.py` … `tests/test_slice9_polish.py`) continue to pass unchanged — they exercise local mode, which is preserved exactly per spec 002 §2.6.

### 4.2 Integration tests

- **`tests/test_remote_mode_smoke.py`** (new): a happy-path remote-mode integration test that mocks the AgentCore SDK's client + the AgentCore Memory SDK. Spins up a fake "runtime" producer that emits a scripted stream; the test drives a full game end-to-end with the human's choices fed by the test harness; asserts the final state matches expectations and that the streamed events flowed through the local UI rendering path. No real AWS calls; the test is fast (<1s) and runs in CI.
- **`tests/test_gateway_mcp_smoke.py`** (new): asserts the runtime-embedded HTTP surface for `/tools/diary/write` and `/tools/diary/read` accepts well-formed payloads, rejects malformed ones, and round-trips a (game_id, player_id, night_index, content) tuple through a mocked AgentCore Memory backend.

### 4.3 End-to-end manual tests

Documented in the new `infra/terraform/README.md` as a checklist a developer runs after first deploy:

1. `aws sso login` → `terraform init` → `terraform apply` → confirm output names a runtime invocation URL.
2. Set `AWS_PROFILE` + `GRAPHIA_RUNTIME_URL` in `.env`.
3. `uv run python -m graphia --remote` → confirm `[remote]` badge appears.
4. Play a complete game; confirm the gameplay matches local-mode behaviour.
5. Confirm the Memory store contains diary records for at least one (game_id, player_id) pair (via AWS console or `aws bedrock-agentcore-memory list-memories`).
6. Open the CloudWatch log group from `terraform output cloudwatch_log_group` and confirm structured traces are present for the session id.
7. `terraform destroy` → confirm `aws bedrockagentcore list-memories` returns empty for the project's agent_id and that re-applying succeeds without name conflicts.

### 4.4 What's deliberately *not* tested in Phase 2

- AgentCore Runtime cold-start latency, throughput, and concurrent-session behaviour — single-developer, single-session use only.
- Bedrock model fallback chains, cross-region failover, throttle handling — Phase 2 assumes the happy path on a stable connection.
- Cedar policy authoring or enforcement — Cedar is descoped per CR 001 amendment.
- Bedrock Guardrails — descoped per CR 001 amendment.
- Multi-player or multi-tenant Runtime usage — explicitly out-of-scope (spec 002 §3.2).