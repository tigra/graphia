# Implementation Tasks: Hosted AgentCore Deployment

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Technical Specification:** [`technical-considerations.md`](./technical-considerations.md)
- **Related ADRs:** [ADR 001 — Hosted AgentCore Runtime with Preserved Local Mode](../../adr/001-hosted-agentcore-with-local-mode.md), [ADR 002 — Runtime-embedded Gateway Tool Handlers](../../adr/002-runtime-embedded-gateway-tool-handlers.md)

This task list is organised into vertical slices. Each slice leaves the app in a runnable state (local mode at minimum; remote mode from Slice 4 onwards) and ends with an automated test plus a user-performed manual smoke test where AWS state needs to be inspected. Slices build strictly on top of one another — do not skip ahead. Per ADR 001's dual-mode posture, local mode must continue to play a full game end-to-end at every slice boundary.

---

## Slice 1: Config refactor + auth posture (local mode preserved)

_The smallest piece of preparatory value: `GraphiaConfig` supports the new SSO-canonical auth posture (per `project_aws_account` memory) and remote-mode awareness, while local mode plays a complete game exactly as before. No AWS / AgentCore work yet._

- [x] Refactor `src/graphia/config.py`: make `bearer_token` optional (`str | None`, defaulting to `None` when `AWS_BEARER_TOKEN_BEDROCK` is unset); flip the `aws_region` default from `eu-north-1` to `us-east-1`; add `remote_mode: bool` (read from `GRAPHIA_REMOTE` env or `--remote` flag); add `runtime_invocation_url: str | None` (read from `GRAPHIA_RUNTIME_URL` env). Remove the `SystemExit` on missing bearer token; replace with a *contradictory-config* check (raise only if `remote_mode=True` and `runtime_invocation_url` is empty). **[Agent: python-backend]**
- [x] Update `src/graphia/__main__.py` to parse `--remote` from `sys.argv` via `argparse` and surface it as `GRAPHIA_REMOTE=1` in `os.environ` before `load_config()` is called. Keep all existing UTF-8 stdio reconfig + `.env` loading behaviour unchanged. **[Agent: python-backend]**
- [x] Confirm `src/graphia/llm.py`'s `get_sonnet()` / `get_haiku()` factories still work with `bearer_token=None`: boto3's default credential chain (e.g. `AWS_PROFILE` resolving an SSO profile) should provide credentials transparently to `ChatBedrockConverse`. No explicit code changes expected; this is a verification task. **[Agent: langgraph-agentic]**
- [x] Add `tests/test_config_auth.py`: parametrised tests covering (a) bearer-token-only env, (b) `AWS_PROFILE`-only env (no bearer), (c) both set, (d) `--remote` flag with `GRAPHIA_RUNTIME_URL` present, (e) `--remote` flag with `GRAPHIA_RUNTIME_URL` missing — must raise the contradictory-config error. **[Agent: testing]**
- [x] Verify automated tests pass: `uv run pytest tests/test_config_auth.py -q` and confirm the existing spec-001 slice tests still pass (`uv run pytest -q`). **[Agent: testing]**
- [x] **USER:** Manual smoke test — set `AWS_PROFILE=my-aws-profile` (and run `aws sso login --profile my-aws-profile` if needed) in the shell, *unset* `AWS_BEARER_TOKEN_BEDROCK` in `.env`, run `uv run python -m graphia` in a real terminal. Confirm a full game plays end-to-end without errors, identical to spec-001 baseline behaviour. _(Agent cannot reliably verify SSO session state; user-performed.)_

---

## Slice 2: AgentCore resource-name discovery + Terraform module skeleton

_The first IaC slice: confirm the actual `aws_bedrockagentcore_*` Terraform resource names from the Registry (Risk 3 from tech-spec §3.2) and lay down the bare Terraform module shell with versions, variables, and outputs. `terraform plan` succeeds against an empty deployment. No real cloud resources yet — this slice gates the rest of the IaC work._

- [x] Use `terraform-mcp-server` to query the `hashicorp/aws` provider's bedrock-agentcore resources: confirm exact resource names for Runtime, Gateway, Gateway target, Memory, and the IAM execution-role pattern. Save findings as `infra/terraform/RESEARCH.md` with provider version, confirmed resource names, and any fallback (e.g., `awscc` provider) needed for resources not yet in `hashicorp/aws`. **[Agent: terraform-aws]**
- [x] Cross-check resource shapes against AWS docs via `aws-knowledge-mcp-server` — what's the minimal viable input set for each AgentCore resource, plus the lifecycle behaviour of Memory on destroy (validates tech-spec §3.2 Risk 2 + spec 002 §2.7 acceptance criterion). Append findings to `infra/terraform/RESEARCH.md`. **[Agent: terraform-aws]**
- [x] Create `infra/terraform/` with the standard layout per `terraform-conventions`: `versions.tf` (exact-pinned terraform + `hashicorp/aws` versions per the research), `variables.tf` (region, account, environment, owner, agent_id — no profile name as a variable; rely on default credential chain), `locals.tf` (tag map: `Project=Graphia`, `ManagedBy=Terraform`, plus `Environment` + `Owner` from variables), `outputs.tf` (placeholders for runtime_invocation_url, cloudwatch_log_group, memory_namespace), `main.tf` (empty for now apart from the provider block), and `README.md` with apply / destroy instructions. **[Agent: terraform-aws]**
- [x] Verify the skeleton: `cd infra/terraform && terraform init && terraform validate && terraform plan -var environment=demo -var owner=<your-email>` succeeds. Plan should show zero resources to add (just provider configuration). **[Agent: terraform-aws]**
- [x] **USER:** Manual smoke test — run `aws sso login --profile my-aws-profile`, then from `infra/terraform/` run `terraform init` + `terraform plan` against the SSO profile. Confirm zero errors and zero unintended resources. _(Confirms the credential chain works end-to-end with Terraform.)_

---

## Slice 3: Runtime container image + minimal AgentCore Runtime resource

_The Runtime infrastructure exists in `us-east-1`: an ECR repo, a built-and-pushed minimal container image, and an `aws_bedrockagentcore_runtime` provisioned against the image. The image's entry-point is a stub that returns a single fake response when invoked — no real graph yet. `uv run python -m graphia` still works locally._

- [x] Add `bedrock-agentcore` as a project dependency: `uv add bedrock-agentcore`. **[Agent: python-backend]**
- [x] Create `src/graphia/runtime/__init__.py` and `src/graphia/runtime/__main__.py`: a stub `AgentCoreApp` that wraps a tiny `lambda payload: {"echo": "stub", "received": payload}` callable and calls `.serve()`. This is the placeholder that Slice 4 will replace with the real compiled graph. **[Agent: langgraph-agentic]**
- [x] Create a `Dockerfile` at the project root that packages the project via `uv sync` and runs `python -m graphia.runtime` as the container entry-point. Include explicit Python version pin and minimal layers. **[Agent: python-backend]**
- [x] Add `aws_ecr_repository` and `aws_bedrockagentcore_runtime` resources to `infra/terraform/main.tf`. Runtime resource points at the ECR image URI, has an IAM execution role (created in the same module) with the minimal policies for Bedrock invoke + CloudWatch logs write. Add `outputs.tf` entries for the ECR repo URL and the runtime invocation endpoint. **[Agent: terraform-aws]**
- [x] Document the image build-and-push prerequisite in `infra/terraform/README.md`: the one-liner that builds the image, logs into ECR, tags by git SHA, and pushes. Note that `terraform apply` is no-op if the image hasn't changed (i.e., the deployment is image-driven). **[Agent: terraform-aws]** _(Build tooling wrapped in repo-root `Makefile` per the build-tooling policy — `make build`, `make push`, `make login-ecr` — and README now references those targets rather than raw `podman push` / `docker push` commands.)_
- [ ] **USER:** Manual smoke test — run the image build-and-push one-liner, then `terraform apply` from `infra/terraform/`. Confirm: ECR repo exists, image tag matches local git SHA, Runtime resource is `ACTIVE` (check `aws bedrockagentcore get-runtime` or console), the Terraform output names a valid invocation URL. Then run `uv run python -m graphia` locally and confirm a full game still plays end-to-end. _(User-performed; involves real AWS provisioning.)_

---

## Slice 4: Local AgentCore client + full remote game (HITL across the wire)

_The headline slice: `uv run python -m graphia --remote` connects to the deployed Runtime, plays a complete Mafia game end-to-end with the human inside the graph, and ends with a decisive winner. The Runtime now hosts the real compiled LangGraph state graph; HITL round-trips work across the wire. No Memory or Gateway yet — diary store is in-process inside the Runtime as a placeholder._

- [ ] Replace the stub `src/graphia/runtime/__main__.py` with the real entry-point: build the compiled `StateGraph` (same `build_graph` function as local mode, with `SqliteSaver` writing to `/tmp/graphia/checkpoints/` inside the container's session-local filesystem per tech-spec §2.5). Wrap it in `AgentCoreApp(graph)` and call `.serve()`. **[Agent: langgraph-agentic]**
- [ ] Create `src/graphia/agentcore_client.py`: thin wrapper around `bedrock_agentcore.client.RuntimeClient` constructed from `config.runtime_invocation_url` + default credential chain. Exposes a single `stream(payload, run_config)` method returning an iterator that matches the local-mode `graph.stream(...)` chunk shape. **[Agent: langgraph-agentic]**
- [ ] Branch `src/graphia/driver.py`'s producer: in remote mode, replace the `graph.stream(...)` call in `_producer` with a call to `agentcore_client.stream(...)`. The `_consume_stream` side is unchanged (it just reads from the `asyncio.Queue`). The resume hand-off (`Command(resume=...)`) routes through the AgentCore client's re-invocation API against the same `thread_id`. **[Agent: langgraph-agentic]**
- [ ] Bake a placeholder in-process `DiaryStore` into the Runtime: store diary entries in a small in-Runtime dict keyed by `(game_id, player_id)`. This is the smoke-test placeholder; Slice 6 replaces it with AgentCore Memory. **[Agent: langgraph-agentic]**
- [ ] Add `tests/test_remote_mode_smoke.py`: end-to-end remote-mode integration test using a *mocked* AgentCore SDK (the `bedrock_agentcore.client.RuntimeClient` is patched at its import boundary, mirroring the spec-001 `safe_llm` fixture). Test drives a complete scripted game; asserts the final state matches expectations and the stream events flowed through the local UI rendering path. **[Agent: testing]**
- [ ] Verify automated tests pass: `uv run pytest tests/test_remote_mode_smoke.py -q`, plus the existing spec-001 slice tests still pass (`uv run pytest -q`). **[Agent: testing]**
- [ ] Rebuild and push the Runtime image, run `terraform apply` (image SHA changes → Runtime picks up the new image). **[Agent: terraform-aws]**
- [ ] **USER:** Manual smoke test — `uv run python -m graphia --remote`. Confirm: the game opens with the Textual UI, plays end-to-end against the hosted Runtime (Night → Day → Night → win), HITL inputs round-trip correctly (the modal pops, your input is accepted, the next super-step runs), and the game reaches a decisive ending. Also run `uv run python -m graphia` (no `--remote`) and confirm local mode is unaffected. _(User-performed; requires real Runtime invocation.)_

---

## Slice 5: `[local]` / `[remote]` corner badge in Textual UI

_Spec 002 §2.2 acceptance: a persistent corner badge tells the player which mode they're in. Cheap to add now that both modes work; keeps the human safe from "did I really launch --remote?" doubt._

- [ ] Add `src/graphia/ui/badge.py`: a `CornerBadge(Widget)` Textual widget with a one-character pad and label text bound at construction time. CSS positions it absolutely in a corner; it doesn't intercept input or steal focus. **[Agent: textual-tui]**
- [ ] Mount the `CornerBadge` in `src/graphia/ui/app.py`: read `GraphiaConfig.remote_mode` at app boot, pass `"[remote]"` or `"[local]"` as the label. Badge visible from the welcome screen through end-of-game. **[Agent: textual-tui]**
- [ ] Add `tests/test_slice_badge.py`: launch `GraphiaApp` via `App.run_test()` in both modes (`remote_mode=True` and `remote_mode=False` via a config-injection fixture); snapshot-assert the badge label is correct. **[Agent: testing]**
- [ ] Verify automated test passes: `uv run pytest tests/test_slice_badge.py -q`. **[Agent: testing]**
- [ ] **USER:** Manual smoke test — run `uv run python -m graphia` and `uv run python -m graphia --remote` and confirm the corner shows `[local]` and `[remote]` respectively. The badge doesn't obscure gameplay text. _(User-performed; UI affordance.)_

---

## Slice 6: AgentCoreMemoryDiaryStore + Memory resource provisioning

_Diary entries now persist in AgentCore Memory in remote mode. Memory store is provisioned via Terraform; the Runtime's in-process placeholder from Slice 4 is replaced by `AgentCoreMemoryDiaryStore`. Per ADR 002 + tech-spec §2.6, one record per diary entry, namespaced via `(game_id, player_id)` in the record body. `terraform destroy` is expected to remove the records as a downstream consequence of destroying the Memory resource — to be validated in Slice 10._

- [ ] Create `src/graphia/diary_store.py`: defines the `DiaryStore` Protocol with `write(game_id, player_id, night_index, content)` and `read(game_id, player_id) -> list[DiaryEntry]`. Provide the `InProcessDiaryStore` (backed by `PlayerState.diary_entries` in `GameState`) and the `AgentCoreMemoryDiaryStore` (wraps `bedrock_agentcore.memory.AgentCoreMemory` per the langgraph-agentcore skill's reference). A factory function selects based on `GraphiaConfig.remote_mode`. **[Agent: langgraph-agentic]**
- [ ] Add an `aws_bedrockagentcore_memory` resource to `infra/terraform/main.tf` keyed by a fixed `agent_id = "graphia-mafia-agent"`. Output the Memory namespace identifier. Update the Runtime's IAM execution role to grant Memory read/write on this resource only. **[Agent: terraform-aws]**
- [ ] Wire the DiaryStore factory into the Runtime entry-point (`src/graphia/runtime/__main__.py`): in remote mode the factory returns `AgentCoreMemoryDiaryStore`; the agent's diary-write/read code paths use the same `DiaryStore` interface regardless of mode (per ADR 001 — parallel implementations behind one interface). Wire it into the actual Night-phase diary-write site that exists in Phase 2 as a smoke-test placeholder. **[Agent: langgraph-agentic]**
- [ ] Add `tests/test_diary_store.py`: parametrised tests exercising the `DiaryStore` Protocol against both implementations. Verify write/read shape, ordering by `night_index`, isolation between `(game_id, player_id)` pairs, append-only semantics. The `AgentCoreMemoryDiaryStore` tests patch the AgentCore SDK at the import boundary (mirroring `safe_llm`). **[Agent: testing]**
- [ ] Verify automated tests pass: `uv run pytest tests/test_diary_store.py -q`. **[Agent: testing]**
- [ ] Rebuild Runtime image (now includes the AgentCore Memory client) and `terraform apply`. **[Agent: terraform-aws]**
- [ ] **USER:** Manual smoke test — play a complete `--remote` game. After the game ends, use `aws bedrock-agentcore-memory list-memories --agent-id graphia-mafia-agent` (or the appropriate CLI per the Slice 2 research) to confirm diary records exist in Memory, one per Night-phase diary write. Confirm the records carry the expected `(game_id, player_id, night_index, content)` shape. _(User-performed; involves AWS-state inspection.)_

---

## Slice 7: Gateway with runtime-embedded tool targets (per ADR 002)

_The Gateway-fronted MCP surface from spec 002 §2.4 + ADR 002: the Runtime exposes HTTP endpoints `POST /tools/diary/write` and `POST /tools/diary/read`; AgentCore Gateway registers them as `api`-type MCP targets. The agent inside the Runtime now invokes diary tools through Gateway-MCP instead of calling `AgentCoreMemoryDiaryStore` directly. Per ADR 002, both target endpoints route back into the same Runtime container — Gateway is the MCP envelope + audit point, not a separate compute layer._

- [ ] Add the HTTP surface to `src/graphia/runtime/__main__.py`: alongside the `AgentCoreApp(graph).serve()` call, mount a tiny ASGI app (FastAPI or `bedrock_agentcore`'s built-in routing if supported) that exposes `POST /tools/diary/write` and `POST /tools/diary/read`. Both handlers delegate to the `AgentCoreMemoryDiaryStore` from Slice 6. **[Agent: langgraph-agentic]**
- [ ] Add `aws_bedrockagentcore_gateway` + two `aws_bedrockagentcore_gateway_target` resources to `infra/terraform/main.tf`, each pointing at the Runtime's HTTP base URL + the relevant tool path. Document the chosen target_type (`api`) and the OpenAPI / MCP schema declarations the Gateway needs for tool registration. **[Agent: terraform-aws]**
- [ ] Refactor the agent-side diary read/write in the Runtime to call the Gateway-published MCP tools instead of the `AgentCoreMemoryDiaryStore` directly. The `AgentCoreMemoryDiaryStore` becomes the *underlying* implementation (called via the HTTP handler); the *agent* calls `gateway_client.invoke("diary_write", ...)`. Local mode remains direct (no Gateway). **[Agent: langgraph-agentic]**
- [ ] Add `tests/test_gateway_mcp_smoke.py`: assert the runtime-embedded HTTP surface accepts well-formed payloads, rejects malformed ones, and round-trips a `(game_id, player_id, night_index, content)` tuple through a *mocked* AgentCore Memory backend. **[Agent: testing]**
- [ ] Verify automated tests pass: `uv run pytest tests/test_gateway_mcp_smoke.py -q`. **[Agent: testing]**
- [ ] Rebuild Runtime image and `terraform apply`. **[Agent: terraform-aws]**
- [ ] **USER:** Manual smoke test — play a complete `--remote` game. Confirm diary writes still land in Memory (`aws bedrock-agentcore-memory list-memories ...`). Confirm via CloudWatch (or `aws bedrock-agentcore get-gateway-target ...`) that Gateway tool invocations are visible — i.e., the agent really did call through Gateway, not bypass it. _(User-performed.)_

---

## Slice 8: AgentCore Observability + 30-day retention + failure modal

_Spec 002 §2.5: structured traces in CloudWatch for every remote-mode session, 30-day log retention, and a Textual failure modal pointing the player at the CloudWatch log group + filter when a remote game crashes. Per the tech spec's design decision: AgentCore Observability provides the agent/tool/Memory traces; LangSmith is opt-in only._

- [ ] Add `aws_cloudwatch_log_group` to `infra/terraform/main.tf` with `retention_in_days = 30`. Enable AgentCore Observability on the Runtime resource (whichever Terraform field surfaces this — confirmed in Slice 2 research). Add the log group ARN as a Terraform output. **[Agent: terraform-aws]**
- [ ] Wire the Runtime to emit a per-session correlation id (the LangGraph `thread_id`) into every trace event so a single game's events are filterable. **[Agent: langgraph-agentic]**
- [ ] Add a `src/graphia/ui/failure_modal.py`: a Textual modal screen shown when the local app catches an unhandled exception during `--remote` play. The modal text includes the CloudWatch log group + a copy-pasteable filter expression (`{ $.thread_id = "<thread>" }`) for the failed session. Local-mode crash behaviour is unchanged (existing banner + log file pointer). **[Agent: textual-tui]**
- [ ] Add `tests/test_slice_failure_modal.py`: in remote-mode test setup, inject an exception during stream consumption; assert the failure modal renders with the expected text including the log group identifier and a thread-id filter expression. **[Agent: testing]**
- [ ] Verify automated tests pass: `uv run pytest tests/test_slice_failure_modal.py -q`. **[Agent: testing]**
- [ ] Rebuild Runtime image and `terraform apply`. **[Agent: terraform-aws]**
- [ ] **USER:** Manual smoke test — play a `--remote` game to completion, then open the CloudWatch log group whose name is in the Terraform output. Confirm structured trace events are present, the `thread_id` correlation id appears, and the log group has `retention_in_days = 30`. Then deliberately misconfigure (e.g., point `GRAPHIA_RUNTIME_URL` at a non-existent URL) to trigger the failure modal; confirm it renders with a valid CloudWatch link/filter. _(User-performed; mixed AWS state + UI smoke.)_

---

## Slice 9: Equivalence tests + end-to-end smoke

_Per tech-spec §4 and Risk "Two parallel DiaryStore impls drift out of sync semantically": exercise `InProcessDiaryStore` and `AgentCoreMemoryDiaryStore` against the same scripted scenarios so behavioural drift surfaces in CI rather than in user-facing remote play._

- [ ] Extend `tests/test_diary_store.py` (from Slice 6) with explicit equivalence-parametrisation: a single set of scenarios (write three entries, read them back, read with no entries, read for a different player_id) runs against both implementations and asserts identical observable behaviour. **[Agent: testing]**
- [ ] Add `tests/test_dual_mode_smoke.py`: drive a complete game once in local mode (real `InProcessDiaryStore`) and once in remote mode (mocked AgentCore SDK + `AgentCoreMemoryDiaryStore`) using the same scripted human inputs. Assert the public log messages, kill log, and final winner match exactly. **[Agent: testing]**
- [ ] Verify the existing full suite still passes: `uv run pytest -q`. Spec-001 slice tests must remain green. **[Agent: testing]**
- [ ] **USER:** Manual smoke test — none required for this slice; the CI suite is the verification surface. _(Optional: play one game in each mode for sanity, but CI covers the equivalence claim.)_

---

## Slice 10: `terraform destroy` cleanup verification + deploy/destroy README

_Spec 002 §2.7: `terraform destroy` removes every resource provisioned in Slices 2–8, including AgentCore Memory data; re-applying from clean state succeeds without naming conflicts. Document the full deploy/destroy lifecycle as a checklist a fresh contributor can follow._

- [ ] Expand `infra/terraform/README.md`: pre-flight checklist (SSO login, environment + owner variables), apply procedure (build image, push to ECR, `terraform apply`), destroy procedure (`terraform destroy`, expected Memory-data behaviour, manual cleanup steps if any), troubleshooting (common AgentCore + Bedrock errors and their resolutions). **[Agent: terraform-aws]**
- [ ] Run `terraform destroy` against the live deployment from a clean state. Use `aws-api-mcp-server` (or the AWS CLI directly) to verify post-destroy: (a) Runtime resource is gone (`aws bedrockagentcore list-runtimes`), (b) Gateway is gone (`aws bedrockagentcore list-gateways`), (c) Memory records for `agent_id=graphia-mafia-agent` are gone, (d) the CloudWatch log group is gone (or in `PENDING_DELETION`). Document any unexpected residual resources as known limitations in `RESEARCH.md`. **[Agent: terraform-aws]**
- [ ] Re-run `terraform apply` from the now-clean state and confirm a successful re-deploy: no name conflicts, all four AgentCore services come back up, the same `terraform output` shape is produced. Rebuild the Runtime image (or reuse the existing ECR tag if it survives the destroy — note which behaviour applies). **[Agent: terraform-aws]**
- [ ] **USER:** Manual smoke test — play one complete `--remote` game against the re-deployed Runtime. Confirm gameplay works end-to-end, diaries appear in the new Memory store, traces flow to the new CloudWatch log group. Then `terraform destroy` one more time as a final cleanup if you don't intend to keep the deployment running. _(Final user-performed validation of the whole Phase 2 deliverable.)_

---

## Coverage / agent assignment summary

All sub-tasks above are assigned to specialist subagents (no `general-purpose` fallback used). MCPs required for verification:

| MCP / Tool | Used in | Purpose |
|---|---|---|
| `terraform-mcp-server` | Slice 2 | Confirm AgentCore Terraform resource names from the Registry |
| `aws-knowledge-mcp-server` | Slice 2 | Validate Memory destroy-lifecycle and AgentCore service-plane behaviour |
| `aws-api-mcp-server` | Slices 6, 7, 8, 10 | Inspect live AWS state during user smoke tests + Slice 10 cleanup verification |
| `langgraph-agentcore` skill | Slices 3, 4, 6, 7, 8 | AgentCore Runtime entry-point, Memory record shape, Gateway target shape, Observability wiring |
| `terraform-conventions` skill | Slices 2, 3, 6, 7, 8 | the configured IaC house style (version pinning, required tags, file layout) |

All MCPs and skills are already installed (via the `/awos:hire` run earlier). No missing capabilities; no installation prerequisites.