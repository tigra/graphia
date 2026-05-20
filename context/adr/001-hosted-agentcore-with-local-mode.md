# ADR 001: Hosted AgentCore Runtime with Preserved Local Mode

- **ADR Number:** 001
- **Title:** Hosted AgentCore Runtime with Preserved Local Mode
- **Status:** Accepted
- **Date:** 2026-05-07
- **Authors:** Alexey Tigarev

---

## 1. Context

Graphia's v1.1 / v1.2 hard scope (per CR 001 + CR 002 amendment) requires demonstrating Bedrock AgentCore — Runtime, Gateway, Memory, Observability — as a real production-deployment target. The decision of *how* to deliver that demonstration was driven by four forcing functions, all of which had to be satisfied simultaneously:

- **The project stakeholder's ask.** The project stakeholder asked the project to demonstrate AgentCore deployment patterns end-to-end. Without this ask, no hosted deployment would be in v1.x scope at all — this is the upstream driver from CR 001.
- **Don't invalidate completed spec 001.** Spec 001 has 65/65 tasks `[x]` and represents real working game mechanics. Any architectural choice that retired or rewrote it would throw away shipped value.
- **Realistic-needs filter applied to the demonstration scope.** Per Graphia's design-driven-by-realistic-needs principle: a reference project that *mocks* AgentCore would mis-represent real practice. The decision had to actually deploy to AgentCore, not simulate it.
- **Fast dev inner loop without AWS round-trips.** Game-mechanics development needs to iterate fast and offline-of-AgentCore; round-tripping through a hosted runtime on every change would slow dev to a crawl and cost money on every test run. This will become *cheaper* still once the Phase 4 Ollama provider lands (no Bedrock cost in pure-local-with-Ollama mode).

The shape of the decision: choose **where the game-engine core runs in the v1.x hosted-deployment story**, and decide **whether to keep a no-AgentCore local path alongside it**.

---

## 2. Alternatives Considered

### Alternative 1: AgentCore Runtime + preserved local mode *(chosen)*

The game-engine core runs as a Bedrock AgentCore Runtime workload in remote mode; the same code runs as a local Python process in local mode, selected via a `--remote` flag at launch. Two parallel implementations of the diary store and the cross-game stats store (in-process / file vs. AgentCore Memory) sit behind the same interfaces.

- **Pros:**
  - Demonstrates AgentCore Runtime + Gateway + Memory in one reference repo — readers can locate every AgentCore concept end-to-end without hopping between projects.
  - Integrated AgentCore Memory + Gateway + Observability surface — single managed surface, no hand-rolled equivalents (DynamoDB / API Gateway / OpenTelemetry) needed.
  - Local mode preserves the spec 001 baseline as a fast dev loop — the completed v1 skeleton stays valid as a no-AWS dev path.
- **Cons:**
  - Lock-in to AgentCore lifecycle — vendor-managed runtime; less control over scheduling, container internals, version pinning.
  - Two parallel store implementations require equivalence tests — DiaryStore and StatsStore each have a local impl and an AgentCore Memory impl; semantic drift between modes is a real bug class.
  - Higher per-game cost in remote mode — ~$1 Bedrock + ~$0.10–0.20 infra per typical game (scale-to-zero keeps idle months at ~$0).
  - Increased complexity due to two modes — broader than just store impls; the dual posture taxes the docs, mental model, testing matrix, and onboarding effort.

### Alternative 2: Cloud-only — drop local mode, deploy only to AgentCore

The hosted AgentCore Runtime is the only run path. Spec 001's local skeleton is retired or downgraded to a documentation reference.

- **Pros:**
  - Single architecture, single set of stores — one run mode, one persistence layer, one set of integration tests.
  - No parallel implementations to keep in sync — eliminates the equivalence-test burden between local and remote mode entirely.
  - Documentation surface halves — only one mode to document; no mode-selection flag, no dual-auth paths.
- **Cons:**
  - Invalidates the completed spec 001 work — 65/65 `[x]` tasks become useless or require rewriting against AgentCore primitives.
  - Forces AWS credentials for every dev iteration — slow inner loop, real per-iteration cost, disenfranchises any contributor without AWS access.
  - Removes the future Ollama provider path's value — Phase 4 Ollama becomes a non-starter without a local mode for it to compose with.

### Alternative 3: Local-only — skip AgentCore deployment entirely

Stay on the spec 001 local skeleton and treat AgentCore as a future research item.

- **Pros:**
  - Simplest possible architecture — single process, single binary, no cloud surface to reason about.
  - Ships fastest — no Terraform module to write, no AgentCore integration.
  - Zero standing infrastructure cost — Bedrock per-game cost only; no AgentCore costs, no idle infra, no observability spend.
- **Cons:**
  - Doesn't meet the project stakeholder's ask — demonstrating AgentCore is the entire reason v1.1 / v1.2 scope was expanded.
  - Leaves AgentCore reference value unrealized — without actual deployment, the project is just another LangGraph demo, not an AgentCore reference.
  - No demonstration of cloud deployment patterns — the Terraform / Runtime / Gateway / Memory / Observability patterns the user wants to internalize don't get exercised at all.

### Alternative 4: Hosted on AWS Lambda (instead of AgentCore Runtime)

Package the game-engine core as a Lambda function or a Lambda Web Adapter container.

- **Pros:**
  - Familiar AWS service — large body of operator knowledge, well-documented IAM/networking story, mature monitoring tooling.
  - Mature, predictable pricing model — per-invocation + duration billing; cost projection is easy and battle-tested.
  - Scale-to-zero behaviour — Lambda also bills only on invocation; no always-on baseline (same Pro as AgentCore Runtime on this axis).
  - Large existing tooling ecosystem — SAM, CDK, Serverless Framework, monitoring add-ons, deployment patterns.
- **Cons:**
  - 15-minute execution limit conflicts with agentic sessions — multi-turn games with HITL interrupts can run longer than Lambda's hard cap.
  - No native agent-runtime semantics — no I/O-wait freedom; pay for the entire wall-clock duration, including time spent waiting for LLM responses.
  - No integrated AI-agent Memory or Gateway — these would have to be hand-rolled (DynamoDB / API Gateway + custom MCP layer).
  - Doesn't demonstrate AgentCore patterns — the entire reason for the v1.1 scope is lost.

### Alternative 5: Hosted on ECS Fargate or EKS

Run the game-engine core as a long-lived container on Fargate or a Pod on EKS, fronted by API Gateway / ALB.

- **Pros:**
  - Full container runtime, no execution-time limit — long-running multi-turn sessions are fine.
  - Familiar Kubernetes / ECS operator model — well-understood orchestration; lots of existing operator knowledge.
  - Container portability — the same container can run on ECS, EKS, Kubernetes elsewhere, or even locally via Docker.
- **Cons:**
  - Always-on cost — running a long-lived container or pod for an intermittent single-tenant game means paying for idle time.
  - Heavyweight infrastructure for the workload shape — VPC, ALB / API Gateway, ECS service definitions, IAM roles, container registry.
  - No native AI-agent abstractions — Memory, Gateway, Observability would all be hand-rolled.
  - Doesn't demonstrate AgentCore patterns — same loss of reference value as Lambda.

---

## 3. Decision

Adopt **Alternative 1**: deploy the game-engine core to **Bedrock AgentCore Runtime** in `us-east-1` for remote mode, while **preserving local mode** as a first-class no-AgentCore run path for game-mechanics development. The two halves are inseparable — picking AgentCore without preserving local mode invalidates the completed spec 001 baseline; preserving local mode without AgentCore fails the project stakeholder's ask. Hence one combined ADR.

Concretely:

- **Remote mode:** AgentCore Runtime hosts the LangGraph game-engine; AgentCore Gateway fronts the per-game diary read/write surface; AgentCore Memory holds both per-game diaries (game-lifetime scope) and long-term cross-game stats (per-player career scope, Phase 3); AgentCore Observability emits traces to CloudWatch. Provisioned via a Terraform module (Phase 2 deliverable).
- **Local mode:** unchanged from spec 001 — single Python process, in-process diary store, JSONL trace log to `GRAPHIA_LOG_FILE`. Local-mode cross-game stats persist to a small file in the local data directory (Phase 3 deliverable).
- **Selection:** a `--remote` CLI flag at launch.
- **Code organization:** parallel implementations of `DiaryStore` and the cross-game stats store sit behind the same interfaces; the LangGraph topology and game logic are mode-agnostic.

---

## 4. Decision Rationale

The rationale for picking Alt 1 over the alternatives sits at the intersection of four categories, ranked here in order of weight:

1. **Stakeholder mandate.** The project stakeholder asked the project to demonstrate AgentCore. Alts 3 / 4 / 5 fail that ask in different ways: Alt 3 doesn't deploy at all; Alts 4 / 5 deploy but to non-AgentCore targets, so they don't demonstrate the patterns the stakeholder wants the project to cover. Alts 1 and 2 both satisfy the ask; the next category broke the tie.
2. **Consistency with existing system.** Spec 001's local skeleton is shipped (65/65 `[x]`) and represents real working game mechanics. Alt 2 (cloud-only) would invalidate that work; Alt 1 preserves it intact as the local-mode baseline.
3. **Default tooling choice.** AgentCore Runtime is AWS's purpose-built agent-hosting service — the AWS-recommended way to host an agent workload in 2026. Alts 4 / 5 (Lambda / Fargate / EKS) are technically viable but go off-piste relative to the platform's own default; for a *reference* project that aims to look like real practice, the default is the right call.
4. **Best fit for the realistic-needs case.** A reference project that mocks AgentCore would mis-represent real practice; a reference project that hand-rolls Memory / Gateway / Observability on Lambda or Fargate would mis-represent the patterns AgentCore is purpose-built for. Only Alt 1 actually shows the AgentCore practice the project is supposed to demonstrate.

The choice was *not* driven by lowest cost (Alt 3 would be cheapest), nor by lowest operational risk (Alt 5 has the most familiar operational model). It was driven by the fit between the project's stated purpose and the available run-time targets.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- AgentCore vendor lock-in for the hosted path — migrating away in remote mode would mean rewriting Memory + Gateway + Runtime integrations against alternative services. Local mode is the escape hatch.
- Two parallel store implementations + equivalence-test burden — `DiaryStore` and the cross-game stats store each have an in-process / file impl AND an AgentCore Memory impl; equivalence has to be exercised by tests.
- Higher per-game cost in remote mode — ~$1 Bedrock + ~$0.10–0.20 infra per typical game; scale-to-zero keeps idle months at ~$0, but active play has a real per-game charge.
- Doubled documentation surface — every state-touching feature has to be specified for both local and remote modes; the two-mode posture taxes the spec / docs / onboarding effort permanently.

**Future implications:**

- Future per-game tools must be MCP-compatible (Gateway-fronted) — tool arg schemas constrained to MCP-compatible primitives. Phase 7 tool-use work has to live within this constraint.
- Future Memory schemas must fit AgentCore Memory's data model — bounded record sizes, per-player namespaces, eventual-consistency semantics. Phase 3 cross-game stats and any future Memory-backed feature inherit these.
- Cross-cutting concerns must be specified for both modes — future Guardrails, audit logging, rate-limiting, content-filtering work has to either land in both modes or be explicitly scoped to one.

**Technical debt incurred:**

- Parallel-impl equivalence tests don't yet exist — Phase 2 / Phase 3 spec deliverable; needs to be written before semantic drift becomes a real source of bugs.
- Two-mode posture means every future feature has to be dual-specified — permanent ongoing tax on spec / test / doc effort; not "debt to be paid off" but a recurring cost of the architecture.

---

## 6. References

- Related ADRs: _none yet — this is ADR 001._
- Related CRs:
  - `context/change-requests/001-agentcore-and-tools-in-scope.md` — introduced AgentCore deployment to v1.1 hard scope (the project stakeholder's original ask).
  - `context/change-requests/002-long-term-memory-for-cross-game-stats.md` — added long-term Memory use-pattern (Phase 3 hard scope) and demoted AI tool-use to Phase 7.
- Related specs:
  - `context/spec/001-playable-skeleton/` — the local-mode baseline this ADR preserves (Status: Completed).
  - `context/spec/002-hosted-agentcore-deployment/` — the Phase 2 functional spec drafted on top of this ADR's decisions (Status: Draft).
- External docs:
  - [Amazon Bedrock AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
  - [Securely launch and scale your agents and tools on Amazon Bedrock AgentCore Runtime](https://aws.amazon.com/blogs/machine-learning/securely-launch-and-scale-your-agents-and-tools-on-amazon-bedrock-agentcore-runtime/)
