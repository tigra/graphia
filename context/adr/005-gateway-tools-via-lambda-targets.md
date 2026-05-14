# ADR 005: Gateway Tools via Lambda Targets (Supersedes ADR 002)

- **ADR Number:** 005
- **Title:** Gateway Tools via Lambda Targets (Supersedes ADR 002)
- **Status:** Accepted
- **Date:** 2026-05-14
- **Authors:** Alexey Tigarev

---

## 1. Context

ADR 002 chose **runtime-embedded handlers** for the Gateway-fronted MCP tool surface in spec 002 §2.4: the same containerised AgentCore Runtime that hosts the LangGraph agent *also* serves a small HTTP surface exposing `diary_write` and `diary_read`; the Gateway registers those endpoints as `api`-type MCP targets pointing back at the Runtime's own URL. The pedagogical claim was *"one container, two responsibilities, simpler ops"*.

Implementation of that pattern (Slice 7 sub-tasks 1–6) surfaced a constraint ADR 002 did not anticipate: **AgentCore Runtime's `protocol_configuration.server_protocol` is mutually exclusive between `HTTP` (the agent stream surface — what Slice 4 deployed and the `--remote` flow depends on) and `MCP` (the runtime-hosted MCP server surface — what Gateway targets of type `mcp_server` require)**. Per the AWS [AgentCore-hosted MCP servers documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html):

> When you configure a Amazon Bedrock AgentCore Runtime with the MCP protocol, the service expects MCP server containers to be available at the path `0.0.0.0:8000/mcp`.

In `HTTP` mode the data plane forwards external `/runtimes/<arn>/invocations` to internal port **8080** for the agent's `@app.entrypoint` stream. In `MCP` mode the data plane forwards external `/runtimes/<arn>/invocations` to internal port **8000** path `/mcp` for the MCP server. A single Runtime resource can be configured for only one of those modes — the docs and the resource schema model it as an enum, not a multi-protocol set. There is no in-container layout that satisfies both modes simultaneously through one `protocol_configuration` setting.

Slice 7's first apply (with `protocol_configuration = MCP` set in Terraform) reached the failure empirically: the Gateway's synchronous `tools/list` probe at `CreateGatewayTarget` time hit `8000/mcp`, the container was listening on `8080/mcp`, and the probe returned "Unable to connect to the MCP server." Slice 7's USER smoke (the headline ADR 002 architectural demonstration) is unverifiable under ADR 002's "one container, two responsibilities" premise.

The decision is forced by an AgentCore architectural constraint that contradicts ADR 002's implementation assumption.

---

## 2. Alternatives Considered

### Alternative 1: Two AgentCore Runtimes, same image

Keep Runtime A (`HTTP` protocol — what's deployed; serves the agent stream) and provision Runtime B (`MCP` protocol — serves the FastMCP tool surface). Same container image; the entry-point detects which port to bind based on an env var or the runtime's configured protocol. Gateway points at Runtime B; the local client keeps pointing at Runtime A.

- **Pros:**
  - **Preserves ADR 002's "tools in a Runtime" framing** — handlers stay on AgentCore-native compute, just in a sibling Runtime.
  - **One container image, two deployable resources** — `aws_bedrockagentcore_agent_runtime.tools` reuses the same ECR image as `aws_bedrockagentcore_agent_runtime.this`.
  - **Pedagogically demonstrates "AgentCore Runtime can host non-agent compute"** — Phase 2's learning surface stays AgentCore-centric.
- **Cons:**
  - **Operationally redundant for a personal reference project** — two Runtimes' worth of cold-start latency, scale-to-zero behaviour, observability surface, IAM scoping, and Terraform-plan output. The footprint doesn't earn its complexity for a single-developer demo.
  - **Container-time port detection is awkward** — the same image needs to behave differently based on env, which complicates the Dockerfile contract and surfaces "which mode am I in" questions throughout the entry-point.
  - **Doesn't actually demonstrate the canonical AgentCore Gateway pattern** — the langgraph-agentcore skill's reference shows Lambda-target Gateway tools as the textbook shape. Re-using the Runtime resource type for tool compute is a non-standard pattern.

### Alternative 2: Lambda targets *(chosen)*

Gateway targets become two zip-deployed Lambda functions — `graphia-diary-write` and `graphia-diary-read` — each packaging the `AgentCoreMemoryDiaryStore` class and the minimal dependencies it needs. Each Lambda owns its own IAM role scoped to `bedrock-agentcore:CreateEvent` / `ListEvents` against the Memory ARN. Gateway invokes them via `target_type = lambda`. The Runtime's `protocol_configuration` reverts to `HTTP` (default); the agent's `@app.entrypoint` stream surface returns; the FastMCP server is deleted from the Runtime container.

- **Pros:**
  - **Matches AgentCore's canonical Gateway pattern** — the langgraph-agentcore skill's reference describes Lambda-target Gateway tools as the textbook shape. Following the path of least resistance through Bedrock's own documented architecture.
  - **Restores Slice 4's working agent flow without modification** — the Runtime stays HTTP-mode; the `--remote` smoke test (Slice 4 USER) keeps passing untouched.
  - **Clean separation: agent compute vs. tool compute** — distinct deployable units, distinct IAM roles, distinct CloudWatch log groups. The boundary between "the agent" and "the tool surface" is structural, not just logical.
  - **Cedar policy seam materialises naturally** — Phase 7's tool-authorization story (mentioned in ADR 002 §5) gets a real cross-service boundary to enforce against; runtime-embedded handlers gave Cedar nothing to bind to.
  - **Smaller blast radius if a tool implementation has bugs** — a bug in the diary handler can't crash the agent loop. The Lambda fails independently; the Runtime keeps serving.
- **Cons:**
  - **More AWS resources to provision and pay for** — two Lambda functions, two IAM roles, two CloudWatch log groups, per-Lambda observability wiring. Compounding overhead for trivial tool implementations.
  - **Diary code lives separately from agent code** — refactoring the diary surface requires editing both the Lambda zip's source (storage) and the agent's call site (via `GatewayMCPDiaryStore`). Two artifacts to keep in sync, even though they share the `AgentCoreMemoryDiaryStore` class via packaging.
  - **Cold-start latency tax per tool call** — first invocation in a session pays the Lambda cold-start cost. Subsequent invocations reuse the warm runtime; for Mafia gameplay (one diary write per AI per Night, ≤30 calls per game) the cumulative cost is small.
  - **Loses ADR 002's pedagogical "runtime-embedded" demonstration** — the alternative architectural pattern from the skill's reference becomes the only one the tutorial shows. ADR 002's earlier framing of "one container, two responsibilities, simpler ops" is retired as a discovered-to-be-incorrect premise.

### Alternative 3: Drop Slice 7 entirely / out of scope

Conclude Gateway-fronted tools aren't viable for Graphia's single-Runtime architecture. Mark Slice 7 as descoped; spec 002 stops at Slice 6 (Memory + DiaryStore working). The agent reaches `_diary_store` directly inside the Runtime; no Gateway, no MCP.

- **Pros:**
  - **Smallest scope of change** — revert Slice 7 sub-tasks 1–6 to their pre-MCP state; no new infrastructure.
  - **Spec 002 closes faster** — three fewer slices to verify before `/awos:verify` can mark Completed.
- **Cons:**
  - **Loses the entire Gateway demonstration value** spec 002 §2.4 committed to as the Phase 2 architectural payoff.
  - **Doesn't address the underlying lesson** — future tools (Phase 7's investigation surface, etc.) will face the same Gateway-vs-direct-call decision; punting now means re-litigating then.
  - **Throws away substantial Slice 7 code investment** — ~600 lines of FastMCP server + GatewayMCPDiaryStore + IAM grants + tests get reverted without yielding any working demonstration.

---

## 3. Decision

Adopt **Alternative 2**: Gateway tools move to two zip-deployed Lambda functions. The Runtime resource reverts to `HTTP` protocol mode (Slice 4's working agent stream surface). The FastMCP server is removed from the Runtime container. ADR 002 is superseded by this ADR.

The agent inside the Runtime continues to call its diary tools through the Gateway (`GatewayMCPDiaryStore` from Slice 7 sub-task 3 stays — it's the client-side MCP path against the Gateway URL); the *target* of those Gateway calls changes from the Runtime's MCP endpoint to the Lambda functions. The Cedar policy seam (Phase 7) gets a real cross-service boundary to bind against.

---

## 4. Decision Rationale

The primary rationale is *match AgentCore's documented canonical pattern* once ADR 002's premise (dual-protocol single Runtime) was empirically disproven. The langgraph-agentcore skill's reference describes Lambda-target Gateway tools as the textbook shape; following AWS's documented architecture is lower-risk than maintaining a non-standard alternative.

The two-runtimes-same-image alternative (Alt 1) preserves ADR 002's "tools in a Runtime" framing but spends real complexity (two Runtimes' observability + IAM + cold-start surfaces; container-time protocol detection) for pedagogical purity that hasn't earned its cost. Slice 7's Gateway demonstration value comes from the *Gateway-MCP-as-tool-plane* claim, not from *where the tool happens to run*; satisfying the former through Lambda targets does not weaken the demonstration. Spec 002 §2.4's acceptance criteria are met whether tools live in Lambdas or in a sibling Runtime.

Dropping Slice 7 entirely (Alt 3) preserves the smallest change but throws away ~600 lines of working Slice 7 code and the Phase 7 Cedar seam ADR 002 §5 explicitly named. The investment-to-value ratio of finishing the slice with Lambdas instead of restarting with descoping is favourable.

Primary rationale category: **consistency with existing system** (AgentCore's documented Gateway-target pattern).

---

## 5. Decision Consequences

**Trade-offs accepted:**

- More AWS resources to provision and pay for — two Lambda functions plus their IAM + CloudWatch surfaces. Compounding overhead acceptable for personal-reference scale; reviewable for multi-tenant work later.
- Diary code split across Lambda zips and agent client — the `AgentCoreMemoryDiaryStore` class is packaged into both Lambda zips; future schema changes require updating both Lambda packages plus the agent's call site.
- Cold-start latency per tool call — Lambda cold starts add ~200–500 ms on first invocation per warm window; subsequent calls reuse the warm runtime. For Mafia gameplay (≤30 diary writes per game) the cumulative cost is small.
- Loses ADR 002's pedagogical "runtime-embedded handlers" claim — superseded by this ADR. Tutorial 002's relevant sections become stale; future tutorial regeneration will explain the Lambda-target shape instead.

**Future implications:**

- **Cedar policy enforcement (Phase 7) has a real cross-service boundary now.** Cedar policies bind to Gateway tool-invocation events; the events trace `agent → Gateway → Lambda` (three distinct identities; Cedar can authorise at each hop). Under ADR 002's runtime-embedded shape, the trace was `agent → Gateway → same-container` (Cedar's value was reduced because there was no real boundary).
- **Phase 7's richer tools (investigation, evidence-builder, Moderator helpers) follow this same pattern by default.** ADR 002 §5 framed runtime-embedded handlers as the *v1.x default* with Lambda targets reserved for richer Phase 7 tools; that framing flips — Lambda targets become the universal default; runtime-embedded is no longer demonstrated.
- **The Runtime container can shrink slightly** — without FastMCP and its transitive deps, the runtime image's footprint reduces; not material in absolute terms (~1–2 MB out of 330 MB), but worth noting.
- **ADR 004 (the IAM-credential-provider workaround) remains in force for the gateway-itself permissions** — but its scope changes: the `make gateway-auth` target now creates `target_type=lambda` targets instead of `mcp_server` targets. The provider gap for `mcp_server` targets is moot; whether the same gap exists for Lambda targets depends on `hashicorp/aws 6.44.0`'s coverage, to be verified during implementation.

**Technical debt incurred:**

- **Tutorial 002 v2 is now meaningfully stale** — its diagram and §2 narratives describe a runtime-embedded MCP surface that no longer exists. Re-running `/awos:tutorial 002` after Slice 7 verifies will refresh it; until then, readers seeing the v2 file may be misled.
- **Spec 002 tech-considerations §2.7 + functional-spec §2.4** describe the original ADR-002-shape Gateway flow. Both need an Update Mode pass to record the Lambda-target shape; alternatively, the spec accepts the supersession as an implementation-detail variation and stays as-is. Pick one in a follow-on `/awos:tech` run.
- **The two Slice 7 sub-task-3 / -4 artifacts** (FastMCP server + its test file) become dead code on the way out; they're removed in the implementation pass, not carried.

---

## 6. References

- Related ADRs:
  - **[ADR 002 — Runtime-Embedded Gateway Tool Handlers (Superseded by ADR 005)](002-runtime-embedded-gateway-tool-handlers.md)** — the now-superseded decision. ADR 002's status flips to "Superseded by ADR 005" with a back-reference in its §7.
  - [ADR 004 — Gateway Target IAM Auth via AWS-CLI Post-Apply Workaround](004-gateway-target-iam-auth-cli-workaround.md) — the provider-gap workaround for `mcp_server` targets; remains in force as a pattern but its specific target type changes (mcp_server → lambda) during this ADR's implementation. Verify whether `hashicorp/aws` exposes `lambda`-type gateway targets cleanly; if so, ADR 004's CLI workaround can shrink scope.
- Related specs:
  - [Spec 002 functional spec §2.4](../spec/002-hosted-agentcore-deployment/functional-spec.md) — the Gateway-fronted MCP demonstration the slice promises.
  - [Spec 002 technical considerations §2.7](../spec/002-hosted-agentcore-deployment/technical-considerations.md) — describes the runtime-embedded handler shape; superseded by this ADR's Lambda shape.
- External docs:
  - [Deploy MCP servers in AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html) — the protocol-exclusivity constraint that forced this ADR. Quotes the `0.0.0.0:8000/mcp` requirement that conflicts with the agent's `8080/invocations` surface.
  - [Gateway → MCP servers (AgentCore developer guide)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html) — the source of the `IamCredentialProvider` requirement (covered in ADR 004); confirms `IAM (SigV4)` outbound auth is supported for AgentCore-Runtime-hosted MCP servers.
  - [`langgraph-agentcore` skill `references/agentcore-deployment.md`](../../.claude/skills/langgraph-agentcore/references/agentcore-deployment.md) — the canonical Lambda-target Gateway pattern this ADR returns to.
