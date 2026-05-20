---
spec: 002-hosted-agentcore-deployment
spec_title: Hosted AgentCore Deployment
introduced_on: 2026-05-20
---

# Concepts introduced in this increment

## Deployment surface

- **Managed-container Runtime as the deployment target** (`agentcore-runtime-as-container`) — AgentCore Runtime is a scale-to-zero, `linux/arm64`-only managed container surface; the deployable unit is a container image in ECR, and the platform handles invocation routing, autoscaling, and idle suspension.
- **Bedrock-AgentCore Python entrypoint** (`bedrock-agentcore-entrypoint-decorator`) — A `BedrockAgentCoreApp` instance plus a function decorated with `@app.entrypoint` plus an `app.run()` call is the full Python contract; the SDK bundles Starlette + Uvicorn internally so the workload looks like a regular function.
- **Async-generator entrypoint streams super-steps as SSE** (`agentcore-async-generator-entrypoint`) — The `@app.entrypoint` handler is an `async def … -> AsyncIterator[dict]`; the SDK detects it via `inspect.isasyncgenfunction` and wraps each yielded dict as one SSE `data:` frame on the wire, so one LangGraph super-step arrives as one client event.
- **Start vs resume payload contract** (`runtime-payload-contract-start-resume`) — The Runtime accepts `{"action": "start", "thread_id", "initial_state"}` and `{"action": "resume", "thread_id", "resume_value"}`; same `thread_id` for start and every resume so the server-side checkpointer lands on the same checkpoint across invocations.
- **Explicit host binding for Podman compatibility** (`runtime-bind-explicit-host`) — `app.run(host="0.0.0.0")` overrides the SDK's `/.dockerenv` auto-detect heuristic, which would otherwise bind localhost-only on container runtimes like Podman that do not create that marker file.

## Auth — workload and client

- **Workload credentials via IAM execution role** (`agentcore-iam-execution-role`) — The Runtime assumes an IAM role with trust principal `bedrock-agentcore.amazonaws.com` and a least-privilege inline policy; the SDK exposes the role's credentials via the standard environment variables so boto3's default chain inside the container picks them up.
- **Standard-credential-chain auth in `GraphiaConfig`** (`boto3-default-credential-chain`) — `GraphiaConfig.load_config()` no longer requires a bearer token; auth flows through boto3's default chain (`AWS_PROFILE` / SSO / instance role). Bearer-token is kept as an optional legacy fallback. Profile name is never hardcoded in source.

## Bedrock model integration (since spec 001)

- **Nova direct on-demand in us-east-1** (`nova-direct-on-demand-us-east-1`) — Graphia invokes Amazon Nova Pro + Nova Lite as the gameplay + roster singletons directly against the foundation-model ARN in us-east-1; no inference profile, no cross-region routing. Retires spec 001's `regional-inference-profile-prefix`.

## Bridging two modes (driver-side)

- **boto3 `invoke_agent_runtime` is the client-side path** (`boto3-invoke-agent-runtime`) — The `bedrock_agentcore` SDK ships server-side primitives only; the client-side surface for invoking a deployed Runtime from outside the container is `boto3.client('bedrock-agentcore').invoke_agent_runtime`.
- **Mode-agnostic consumer via shared chunk shape** (`mode-agnostic-consumer-via-shared-chunk-shape`) — `_consume_stream` reads `{node_name: update}` dicts from an `asyncio.Queue` regardless of source; the producer side branches local (`graph.stream(...)`) vs remote (`client.stream(...)`) but emits the same chunk shape, so HITL, UI rendering, and resume hand-off are mode-agnostic.
- **Dual-mode config with contradiction check** (`dual-mode-config-with-contradiction-check`) — `GraphiaConfig` gains `remote_mode` and `runtime_invocation_url` fields; `load_config()` raises only when the state is inconsistent (remote requested but no URL), not when any single auth path is configured.
- **Argparse → env → typed config bridge** (`argparse-env-bridge`) — `--remote` is parsed by argparse, then promoted via `os.environ["GRAPHIA_REMOTE"] = "1"` *before* `load_config()` runs — so the typed dataclass remains the single source of truth, no parallel "is the flag set?" check.
- **Corner-docked mode badge** (`corner-badge-widget`) — A top-right `[local]` / `[remote]` indicator mounted in the Textual layout from `config.remote_mode`, so the player can never confuse which mode they are playing in.

## Per-game state on AWS

- **DiaryStore Protocol with two implementations** (`diary-store-protocol-abstraction`) — One `Protocol` with `write(game_id, player_id, night_index, content)` and `read(game_id, player_id) -> list[DiaryEntry]`; two implementations (`InProcessDiaryStore` for local, `AgentCoreMemoryDiaryStore` / `GatewayMCPDiaryStore` for remote) interchange behind it.
- **Diary store factory with precedence** (`make-diary-store-factory`) — `make_diary_store(config)` picks the Gateway-MCP impl when a gateway URL is configured, falls through to the direct Memory client when only a memory id is set, and lands on the in-process map otherwise.
- **Gateway-fronted MCP diary tools** (`gateway-mcp-diary-tools`) — AgentCore Gateway exposes `diary_write` / `diary_read` Lambdas as MCP tools; the in-Runtime store calls them with SigV4-signed httpx requests, the Lambdas talk to AgentCore Memory. The agent never speaks to Memory directly — it goes through the same governance layer a real product would.
- **AgentCore Memory event model for diaries** (`memory-diary-event-shape`) — Each diary entry is one `MemoryClient.create_event()` with `actor_id=player_id`, `session_id=game_id`, JSON payload `{kind, game_id, player_id, night_index, content}`, and a zero-padded `night_index` in event metadata so reads land sorted.
- **Night-close diary write/read round-trip with graceful fallback** (`night-close-diary-roundtrip`) — `night_close` reads each surviving AI's prior entries on Night 2+ before writing the new entry, each call guarded by a try/except so a single failure logs and continues without crashing the game. The same node code runs in both modes; only the injected store differs.

## Infrastructure as Code

- **Terraform run inside a pinned container** (`terraform-via-pinned-container`) — A `./tf` wrapper auto-detects Podman or Docker, pulls a pinned `hashicorp/terraform:1.13.1` image, mounts the project + SSO cache, and forwards `AWS_PROFILE`. Removes "works on my Terraform version" drift.
- **AgentCore resources in the AWS provider** (`agentcore-runtime-tf-resource-quirks`) — `aws_bedrockagentcore_agent_runtime` (note the `_agent_` infix in the Terraform name, even though CloudFormation calls it `AWS::BedrockAgentCore::Runtime`); the resource exposes no `invocation_url` attribute — clients invoke against the `agentRuntimeArn` via the `InvokeAgentRuntime` data-plane API.
- **Required tags via provider `default_tags`** (`provider-default-tags-block`) — One `local.common_tags` map (`Project`, `ManagedBy`, `Environment`, `Owner`) applied to every taggable resource by the provider; no per-resource `tags = …` repetition.
- **Single name prefix with regex-aware variants** (`name-prefix-with-regex-aware-variants`) — All resource names derive from `local.name_prefix = "graphia-${var.environment}"`; resources with stricter regexes (AgentCore Runtime requires underscores) get derived variants via `replace()` + `substr()`.
- **ECR force-delete safeguard** (`ecr-force-delete-safeguard`) — `var.ecr_force_delete` defaults to `false` so destroy refuses to drop the ECR repo while it has images; override is a two-step (targeted apply, then destroy) because the AWS provider reads `force_delete` from prior state at destroy time.
- **Gateway + Lambda + zip-build pipeline** (`gateway-mcp-target-and-zip-build`) — Each diary tool is shipped as a Python 3.13 Lambda whose zip is built by a Makefile pattern rule (pip-install `manylinux2014_x86_64` wheels into a staging dir, handler at zip root); Terraform wires `aws_bedrockagentcore_gateway` + two `aws_bedrockagentcore_gateway_target` resources whose tool schemas declare the call signatures Gateway publishes over MCP.
- **CloudWatch vended log + trace delivery** (`cloudwatch-vended-log-delivery`) — `aws_cloudwatch_log_delivery_source` (`APPLICATION_LOGS`, `TRACES`) and `aws_cloudwatch_log_delivery_destination` (CloudWatch Logs / X-Ray) declare the runtime's telemetry routing without touching the platform-managed log group itself; a separate `aws_cloudwatch_log_resource_policy` lets X-Ray write to `aws/spans` for Transaction Search.

## Container build & deploy loop

- **Multi-stage uv-driven Dockerfile** (`multi-stage-uv-dockerfile`) — uv-based builder stage installs deps from `pyproject.toml` + `uv.lock` before copying source, so source edits don't bust the dep-install cache. Final stage copies the `.venv` only.
- **Makefile as project-wide task-runner** (`makefile-as-task-runner`) — Repo-root Makefile orchestrates `./tf` + container-runtime + AWS CLI under named workflow composites (`make deploy`, `make redeploy`, `make destroy`, `make play-remote`, `make inspect-diary`); defaults pull from `git config user.email` and `git rev-parse --short HEAD`.
- **Image-driven Runtime deploys** (`image-driven-deploys`) — The AgentCore Runtime resource interpolates `container_uri = "<repo>:${var.image_tag}"`, so bumping the tag string is what triggers a roll. `make redeploy` always passes the current git SHA as the tag.
- **Bootstrap-then-apply first deploy** (`bootstrap-then-apply`) — First-time deploy chicken-and-egg: a targeted apply (`./tf apply -target=aws_ecr_repository.runtime`) creates only the ECR repo so `make push` has somewhere to push to; a full apply follows.
- **Makefile deploy next-step hint** (`makefile-deploy-next-step-hint`) — Both `deploy` and `redeploy` end with an echoed *"Next: launch a game against the deployed Runtime with: make play-remote"* so the developer never has to remember the launch command.

## Observability

- **Per-invocation OTEL root span** (`per-invocation-root-span`) — `runtime_invocation_span()` opens one root span named `graphia.runtime.invocation` per `start`/`resume` call; kept active while the graph streams, so every LangChain-instrumented child span nests under it instead of producing many disconnected top-level spans.
- **OTEL baggage `session.id` for trace-tree grouping** (`otel-baggage-session-grouping`) — `stamp_trace_thread_id()` writes the LangGraph `thread_id` to the `session.id` baggage entry; AWS's OTEL configurator promotes that onto every span so the GenAI Observability console collapses one game's spans into one navigable session tree.
- **OpenInference LangChain instrumentor for trace tree** (`openinference-langchain-instrumentation`) — Generic AWS Distro for OpenTelemetry does not auto-instrument LangChain / LangGraph; AgentCore's auto-loader picks up the explicit `openinference.instrumentation.langchain` entrypoint and produces the nested LangChain spans the trace tree depends on. CR 003 root cause.
- **ContextVar thread_id binding for log filtering** (`contextvars-thread-id-binding`) — Module-level `_THREAD_ID` ContextVar set at invocation start; a `ThreadIdLogFilter` injects it onto every log record so the JSON-formatted CloudWatch line carries `thread_id`, and a `{ $.thread_id = "<thread>" }` filter selects exactly one game.
- **Runtime IAM observability permissions** (`runtime-iam-observability-permissions`) — The Runtime execution role needs `logs:CreateLogGroup/Stream`, `logs:DescribeLog*`, `logs:PutLogEvents`, and `xray:PutTraceSegments` / `xray:PutTelemetryRecords` for the platform-managed log group + trace export to work. Missing these is what kept the trace tree empty before CR 003.

## Error surface

- **Failure modal as the remote error surface** (`failure-modal-as-error-surface`) — When a remote-mode invocation raises, the Textual `FailureModal` renders the captured `thread_id`, the CloudWatch log group, and a copy-pasteable `{ $.thread_id = "…" }` filter; we accepted this over a dedicated pre-launch refusal (CR 004) because mid-game failure with copy-pasteable coordinates is more honest and cheaper than guessing what to validate up front.

## Equivalence & introspection

- **Dual-mode equivalence smoke test** (`test-dual-mode-equivalence`) — `test_local_and_remote_full_game_produce_identical_public_output` runs a full game in each mode with identical seed + scripted LLM responses + scripted human inputs; asserts identical public messages, kill log, and winner. Proves the remote leg adds no observable gameplay difference.
- **`inspect-diary` CLI for Memory introspection** (`inspect-diary-cli-tool`) — `python -m graphia.tools.inspect_diary` walks AgentCore Memory's actor/session/event tree, decodes the JSON entries, and prints a table or JSON dump. Wrapped behind `make inspect-diary` for the post-game "did the round-trip really land?" check.
