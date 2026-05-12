# ADR 002: Runtime-embedded Gateway Tool Handlers (vs. Lambda Targets)

- **ADR Number:** 002
- **Title:** Runtime-embedded Gateway Tool Handlers (vs. Lambda Targets)
- **Status:** Accepted
- **Date:** 2026-05-12
- **Authors:** Alexey Tigarev

---

## 1. Context

Phase 2 of Graphia (Hosted AgentCore Deployment) requires a Gateway-fronted MCP surface for per-game diary read/write — that's the v1.x AgentCore Gateway demonstration, committed in spec 002 §2.4. The decision is *not* whether to have a Gateway surface (that's settled); it's **how to back the tool implementations**: with separate Lambda functions registered as Gateway targets (the canonical pattern shown in the `langgraph-agentcore` skill's reference material), or with HTTP handlers embedded in the same Runtime container that serves the agent.

Three forcing functions drove the choice:

- **Spec 002 §2.4 requires Gateway-fronted diary R/W** — the functional spec commits to the Gateway-fronted MCP surface as the Phase 2 demonstration. The hard constraint is the *Gateway surface itself*; the tool-backing shape is the design space.
- **Personal reference project — minimise moving parts** — Graphia is a personal reference / learning project (product-definition §1.2 personas), not a multi-tenant production system. Operational overhead per AWS resource compounds quickly when the underlying work is trivial.
- **Phase 2 diary content is placeholder (smoke-test scope)** — per spec 002 §2.4, Phase 2 only needs to *exercise* the diary path with placeholder content; rich AI-generated diaries land in Phase 6. The tool implementation in Phase 2 is trivial — it doesn't justify its own compute surface.

The decision was made on 2026-05-12 while drafting the Phase 2 technical considerations via `/awos:tech`. The langgraph-agentcore skill's reference (`references/agentcore-deployment.md`) was loaded during drafting and shows the Lambda-target pattern as the canonical CDK shape.

---

## 2. Alternatives Considered

### Alternative 1: Runtime-embedded handlers exposed back via Gateway *(chosen)*

The same containerised Runtime that serves the LangGraph agent also exposes a small HTTP surface (`POST /tools/diary/write`, `POST /tools/diary/read`). The Gateway registers these endpoints as `api`-type MCP targets pointing back at the Runtime's HTTP base URL. One deployable unit; the Gateway sits in front of the Runtime for tool routing.

- **Pros:**
  - **Fewer AWS resources to provision and pay for** — no separate Lambda functions, no extra IAM roles for Lambda execution, no separate CloudWatch log groups per Lambda. Simpler `terraform apply` output, smaller blast radius if something goes wrong.
  - **Diary code lives next to the agent that uses it** — single repository, single deployable unit, single Python module hierarchy. Refactoring the diary surface doesn't require coordinating two separate deployable artifacts.
  - **No Lambda cold-start latency per tool call** — first tool call in a session doesn't pay the Lambda cold-start tax. The Runtime container is already warm; the diary endpoints respond immediately.
  - **Tighter end-to-end debug trail** — when the agent calls the diary tool, the whole call lives in one CloudWatch log stream (the Runtime's). With a separate Lambda, the agent's trace and the tool's trace are split across log groups and harder to correlate.
- **Cons:**
  - **Gateway demonstration is thinner** — Gateway's role degrades to "MCP envelope + routing back to the Runtime" — no separate compute layer to showcase how Gateway sits in front of distinct services. The pattern shown is real but less illustrative of a multi-service AgentCore deployment.
  - **Runtime container has dual responsibility** — the same container serves both the agent loop and the tool endpoints. Architecturally this blurs the boundary between "the agent" and "the tool surface"; complicates resource sizing (the container has to accommodate both workloads).
  - **Deviates from the skill's recommended CDK pattern** — the bundled `langgraph-agentcore` skill's reference shows Lambda targets for Gateway tools. Deviation requires explicit justification (this ADR) and means future contributors may default-revert without checking.
  - **Harder to extract a tool to its own service later** — if a future increment needs the diary surface to scale or deploy independently (e.g., multi-tenant Phase 7 work), extracting it from the Runtime is more work than swapping in a Lambda target later — the seam between agent and tool isn't sharp.

### Alternative 2: Lambda functions for `diary_write` / `diary_read`

Two small Lambda functions wrap the AgentCore Memory SDK calls; the Gateway registers them as `target_type="lambda"` MCP targets. The agent inside the Runtime discovers them via MCP and invokes them through Gateway. Matches the `langgraph-agentcore` skill's canonical CDK pattern.

- **Pros:**
  - **Clean separation: agent compute vs. tool compute** — distinct deployable units, distinct IAM roles, distinct CloudWatch log groups. Architecturally cleaner: "the agent" and "the tool surface" are obviously different things sitting at different addresses.
  - **Matches the langgraph-agentcore skill's reference pattern** — the bundled skill's CDK example uses `gateway.add_target("name", target_type="lambda", function=...)`. Following the reference pattern means future contributors recognise the shape, and tutorial output will read more "textbook-AgentCore".
  - **Gateway demo has real teeth** — cross-service authorization, real audit trail of cross-service calls, real demonstration of how AgentCore Policy / Cedar would slot in front of tool invocations. The Gateway is doing something only Gateway can do.
  - **Smaller blast radius if a tool implementation has bugs** — a bug in the diary handler can't crash the agent loop. The Lambda fails independently; the Runtime keeps serving. With runtime-embedded, a tool-handler exception could in principle disrupt the agent's session.
- **Cons:**
  - **More AWS resources to provision, monitor, and pay for** — two Lambda functions, two IAM roles, two CloudWatch log groups, separate observability wiring. Compounding overhead for a personal reference project where the tool implementations are trivial.
  - **Diary code lives separately from agent code** — refactoring the diary surface requires editing both the Runtime's code (which has the calling logic) and the Lambda's code (which has the storage). Two artifacts to keep in sync.
  - **Larger IAM and observability surface to maintain** — each Lambda needs its own role + policy + log-group retention. More moving parts in the Terraform module, more places things can drift out of policy.

---

## 3. Decision

Adopt **Alternative 1**: the Phase 2 Gateway-fronted diary surface uses **runtime-embedded HTTP handlers**. The same container that serves the LangGraph agent also serves `POST /tools/diary/write` and `POST /tools/diary/read`. The Gateway registers these endpoints as `api`-type MCP targets routing back at the Runtime's HTTP base URL.

The decision applies **per-tool**, not project-wide. Future tools — most notably the Phase 7 AI tool-use surface (investigation tool, evidence-builder tool, Moderator helpers) — may ship as Lambda targets if those richer tools genuinely benefit from isolation, independent scaling, or cross-service authorization. This ADR establishes the *default* posture for v1.x and the *frame* for evaluating future tool-backing choices, not a universal rule.

---

## 4. Decision Rationale

**Primary rationale (synthesised from the user's framing during the ADR interview):** lower complexity + faster to ship + Lambda tools can be demonstrated in later stages.

The reasoning unpacks into three threads:

- **Lower complexity for v1.x.** The diary tool implementations are trivial (wrap a Memory `store` and a Memory `search`); spinning up two Lambda functions, two IAM roles, two CloudWatch log groups, and the Lambda-target Gateway registrations to host that work fails the cost/value test. The runtime-embedded approach absorbs the work into the container that already exists.
- **Faster to ship Phase 2.** Fewer Terraform resources to wire, one container image to build instead of one image plus two Lambdas, one observability pipeline to verify instead of three. Reduces the Phase 2 deliverable's surface area without changing what spec 002 promises.
- **Lambda tools remain available for later stages.** This ADR doesn't *reject* the Lambda-target pattern; it defers it. When Phase 7's richer tools land (or when a future tool genuinely benefits from compute isolation, independent scaling, or a cross-service authorization seam for Cedar), they can ship as Lambda targets. The reference pattern from the `langgraph-agentcore` skill will still be applicable then — to the *right* tools, in the *right* phase, instead of to placeholder Phase 2 work.

The choice was *not* driven by lowest absolute cost (Alt 2's Lambda costs are small), nor by ignoring the skill's recommended pattern (the deviation is deliberate and bounded), nor by stakeholder mandate (the Phase 2 stakeholder spec didn't dictate tool-backing shape). It was driven by proportionality between implementation surface and actual work — apply the more complex pattern when it earns its complexity.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- **Thinner Gateway demonstration** — Gateway is reduced to MCP envelope + routing rather than fronting distinct compute. The pattern shown is real but less illustrative of multi-service AgentCore deployments.
- **Runtime container has dual responsibility (agent + tool serving)** — mixed concerns; the same container has to accommodate both workloads. Resource sizing and scaling get coupled.
- **Deviation from skill's recommended CDK pattern** — future contributors / tutorial readers may default-revert to the recommended Lambda pattern without checking. This ADR is the durable justification record.
- **Smaller blast-radius isolation foregone** — a tool-handler exception could in principle disrupt the agent's session (vs. an isolated Lambda failure). Mitigated by careful exception handling in the tool layer; not eliminated.

**Future implications:**

- **Lambda targets remain available per-tool for later phases.** This ADR establishes that "runtime-embedded vs. Lambda" is a *per-tool* decision, not project-wide. Phase 7 AI tool-use (investigation, evidence-builder, Moderator helpers) can ship as Lambda targets if those richer tools genuinely benefit from isolation — the choice here doesn't preclude that.
- **Cedar policy seam deferred to Phase 7+.** Cedar policies would naturally enforce at the cross-service boundary. Runtime-embedded handlers have no cross-service boundary, so Cedar's value is reduced for the diary surface. If Cedar lands later, it'll be most useful for Phase 7's externally-deployed tools.
- **Future tools needing independent scaling must extract.** If a tool ever needs to scale or deploy independently of the agent (different memory profile, different concurrency limits, different deployment cadence), it'll need to be lifted out of the Runtime container. The seam between agent and tool isn't sharp — extraction is real work, not a one-line swap.

**Technical debt incurred:**

- None material. The diary surface is two endpoints with placeholder content; lifting them out to Lambda later is a known shape of change, not open-ended risk. The runtime-embedded choice doesn't manufacture debt — it accepts a small coupling in exchange for proportional simplicity, and that coupling is bounded by the surface area of the two endpoints.

---

## 6. References

- Related ADRs:
  - [ADR 001 — Hosted AgentCore Runtime with Preserved Local Mode](001-hosted-agentcore-with-local-mode.md) — parent ADR; this one is a tactical follow-on within ADR 001's hosted-deployment scope. The "Gateway in Phase 2 fronts the diary surface" decision in ADR 001 §5 is the upstream constraint that this ADR specialises.
- Related CRs:
  - [CR 001 — AgentCore + tools in scope](../change-requests/001-agentcore-and-tools-in-scope.md) — the upstream CR that introduced AgentCore Gateway to v1.x scope. Names *that* the Gateway is in scope, not *how* its tools are backed.
- Related specs:
  - [Spec 002 functional spec §2.4 + §2.7](../spec/002-hosted-agentcore-deployment/functional-spec.md) — the acceptance criteria this ADR's implementation pattern satisfies (§2.4 names the Gateway-fronted diary surface; §2.7 names the destroy-cleanup story this choice simplifies).
  - [Spec 002 technical considerations §2.5 + §2.7](../spec/002-hosted-agentcore-deployment/technical-considerations.md) — where the runtime-embedded handlers are concretely specified at the architectural level. This ADR is the durable justification record for the choice the tech spec commits to.
- External docs:
  - [`langgraph-agentcore` skill — `references/agentcore-deployment.md`](../../.claude/skills/langgraph-agentcore/references/agentcore-deployment.md) — where the Lambda-target CDK pattern is documented as the recommended shape. This ADR's chosen alternative deviates from that pattern with the rationale and consequences captured in §4 and §5 above.