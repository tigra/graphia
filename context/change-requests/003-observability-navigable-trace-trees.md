# Change Request: AgentCore Observability Delivers Navigable Per-Session Trace Trees

- **CR ID:** 003
- **Date:** 2026-05-15
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [ ] `context/product/product-definition.md` — section: [name]
- [x] `context/product/roadmap.md` — phase / item: Phase 2 → "AgentCore Observability"
- [x] `context/spec/002-hosted-agentcore-deployment/functional-spec.md` — section: §2.5 "Observability — verifying AgentCore was actually involved"
- [x] Other: spec 002 `technical-considerations.md` §2.11 (Observability wiring) and `tasks.md` Slice 8

**Context:** The Phase 2 "AgentCore Observability" deliverable — its roadmap item, its functional-spec §2.5 acceptance criteria, its technical-considerations §2.11 wiring notes, and its Slice 8 task list — is refined so the observability outcome is a navigable per-session trace tree, not only structured log events.

---

## 2. Summary of Change

The Phase 2 AgentCore Observability deliverable is extended: in addition to structured, session-correlated log events, remote-mode play must produce a navigable per-session **trace tree** in AgentCore's GenAI Observability view — a parent/child span hierarchy of one game's agent activity (graph invocation, per-turn model calls, Gateway-fronted tool calls), correlated by the session's game identifier. Achieving this requires the deployed Runtime to be instrumented with OpenTelemetry.

---

## 3. Driver (Why This Change?)

**Primary driver:**

- [ ] **User / stakeholder feedback**
- [x] **Implementation learnings** — something discovered while building that invalidated an earlier assumption
- [ ] **New external constraint**
- [ ] **Strategic pivot**
- [ ] **Error correction**
- [ ] **Scope adjustment**
- [ ] **Other**

**What was the previously-agreed assumption?** The Phase 2 observability work, as scoped in Slice 8, would yield the inspectable trace view the roadmap promises ("emit traces so a player or operator can inspect what the agents did during a game") and that technical-considerations §2.11 assumes ("model-call boundaries are visible in the AgentCore agent-decision traces").

**What changed about that assumption?** The Slice 8 deployment smoke test showed the observability work as planned delivers only flat, session-correlated log events — AgentCore's GenAI Observability console shows individual entries, not a navigable span tree — because producing a trace tree requires an explicit OpenTelemetry instrumentation step that was never on the slice plan.

**Detailed reasoning:** After deploying the Slice 8 build and playing a complete remote-mode game, the deployed CloudWatch log group showed structured events carrying the session's game identifier — the log surface works. But the same session in AgentCore's GenAI Observability console showed only individual flat entries, no parent/child span hierarchy. A trace tree is built from OpenTelemetry spans, and the Runtime workload was never instrumented to emit them; Slice 8 delivered the log surface and the AWS-side trace plumbing but not the in-Runtime instrumentation that makes the trace view navigable. No later slice (Slice 9 = equivalence tests, Slice 10 = teardown) and no later roadmap phase (3–7) plans observability work — Slice 8 is the only home for it. Left unaddressed, the Phase 2 observability commitment closes under-delivered.

**Could this have been anticipated earlier?** Partly — §2.5 listed the expected trace events (per-tool-call, per-model-roundtrip), effectively spans, but neither the spec nor the Slice 8 task breakdown named the Runtime-instrumentation step that produces them; the gap was only visible once a real deployment could be inspected in the console.

---

## 4. Nature of Change

- [ ] **Additive**
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope**

Sharpens the §2.5 observability success criterion and the Slice 8 "Enable AgentCore Observability" scope from "structured trace events" (which flat logs satisfy) to an explicit navigable per-session trace tree. Adds no new capability area; removes nothing.

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section) | What changes | Already implemented? |
| ---------------------------------- | ------------ | -------------------- |
| `context/product/roadmap.md` — Phase 2 "AgentCore Observability" | Item clarified: "inspect what the agents did" explicitly includes a navigable per-session trace tree, not only structured log events. | No |
| `context/spec/002-hosted-agentcore-deployment/functional-spec.md` §2.5 | Observability acceptance criteria extended to require the navigable per-session trace tree as an outcome of remote-mode play, alongside the existing structured-log criteria. | Partially — log surface delivered; trace tree not. |
| `context/spec/002-hosted-agentcore-deployment/technical-considerations.md` §2.11 | The "model-invocation tracing not custom-instrumented in Phase 2" stance is reconciled: OpenTelemetry instrumentation makes agent/model/tool-call boundaries visible as spans. | Partially — log-side wiring done; trace-instrumentation stance changes. |
| `context/spec/002-hosted-agentcore-deployment/tasks.md` — Slice 8 | Slice 8 gains a follow-on task: instrument the Runtime with OpenTelemetry and confirm the trace tree in the AgentCore Observability console. | Partially — Slice 8 in progress; log-side sub-tasks done. |

**Rework / migration required:** The structured-log observability already delivered in Slice 8 remains valid and unchanged — the trace tree is added alongside it, not in its place. §2.5 gains an added acceptance criterion; §2.11's "not custom-instrumented" stance is revised; Slice 8's task list gains the instrumentation follow-on. No previously-completed work is invalidated.

---

## 6. Decision

- **Decision:** Accepted
- **Decided by:** Alexey Tigarev
- **Decided on:** 2026-05-15
- **Rationale:** The trace tree is the observability experience the roadmap and §2.11 already assume, and Slice 8 is the only place in the plan where it can land; the instrumentation is a contained addition to the in-flight slice.

---

## 7. Follow-up Actions

- [x] Update `functional-spec.md` §2.5, `technical-considerations.md` §2.11, `tasks.md` Slice 8, and `roadmap.md` Phase 2 wording to reflect the trace-tree criterion. _(Done — see §8.)_
- [x] Implement the OpenTelemetry instrumentation of the Runtime under Slice 8 and redeploy. _(Done — together with the IAM root-cause fix; see §8.)_
- [ ] Consider `/awos:adr` to record OpenTelemetry as the tracing mechanism for AgentCore Observability. _(Not needed — ADOT + the Runtime execution-role IAM are the AWS-documented recipe, not a novel architectural choice; captured in technical-considerations §2.11 and `infra/terraform/RESEARCH.md` instead.)_
- [ ] Re-run `/awos:verify` for spec 002 once all Slice 8 work is complete.

---

## 8. Addendum (2026-05-18) — Implementation outcome: the root cause was IAM, not the instrumentation recipe

CR 003 was logged on the assumption that the flat trajectory would be fixed by correcting the Runtime's OpenTelemetry instrumentation recipe. Implementation proved that assumption only half-right.

**What the instrumentation needed.** Three iterations were deployed before any in-app span appeared: the ADOT distro plus a real LangGraph GenAI instrumentor (`openinference-instrumentation-langchain`) initialised programmatically; a per-invocation root span; and the `opentelemetry-instrument` ADOT wrapper in the Dockerfile (the `bedrock-agentcore` SDK relies on ADOT having set up the `TracerProvider` before the app starts). All genuinely required — but none of it produced a single span on its own.

**The actual root cause — a missing IAM grant.** The Runtime's execution role was scoped (in Slice 3) to write CloudWatch Logs only to the Terraform-made `graphia-demo-runtime` log group, with no `logs:CreateLogGroup` and **no X-Ray permissions at all**. Per the AWS "IAM Permissions for AgentCore Runtime" doc, an AgentCore Runtime execution role needs `xray:PutTraceSegments` / `PutTelemetryRecords` / sampling reads, plus `logs:CreateLogGroup` + `CreateLogStream` + `PutLogEvents` on `/aws/bedrock-agentcore/runtimes/*`. Without the X-Ray grant the in-Runtime exporter could not ship spans; without the logs grant the container's own service log group was never created. The fix was a Terraform IAM-policy change — no image rebuild.

**Outcome — delivered.** A real `--remote` game now produces a navigable per-session trace tree: ~544 spans, deeply nested (root `graphia.runtime.invocation` → LangGraph node spans → `ChatBedrockConverse` / Nova model-call spans), scopes `openinference.instrumentation.langchain` + `botocore` + `graphia.runtime`, visible in the AgentCore GenAI Observability console. A live verification harness (`tests/test_remote_observability_live.py`, `make verify-observability`) drives the deployed Runtime and asserts the trace-tree contract against real `aws/spans` data.

**Lesson.** The instrumentation recipe and the IAM grant were *both* required; the recipe lived in code and drew the attention, while the IAM gap was the silent blocker. It surfaced only by diagnosing against real deployed state — a log group every other runtime in the account had and Graphia did not — not by reasoning from docs.