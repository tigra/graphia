# Change Request: AgentCore Deployment + AI Tool-Use Promoted to v1.1 Scope

- **CR ID:** 001
- **Date:** 2026-05-05
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [x] `context/product/product-definition.md` — section: §1 Big Picture, §2 Product Experience, §3 Project Boundaries
- [x] `context/product/roadmap.md` — phase / item: Phase 2 → "Hosted Deployment / Bedrock AgentCore Deployment"
- [ ] `context/spec/[NNN-slug]/functional-spec.md` — section: [name]
- [x] Other: net-new in-scope feature surface (AI tool-use) not previously on roadmap

**Context (1–2 sentences):** The product definition (§1.1, §1.4, §2.1, §2.2, §3.1) was updated to make Bedrock AgentCore deployment a v1.1 hard requirement and to introduce LangGraph tool-use by AI players (investigation, evidence-builder, Moderator helpers) as a core feature. The roadmap's existing Phase 2 "Hosted Deployment" item, previously framed as an optional Runtime-only future deliverable, is implicitly rescoped by this change.

---

## 2. Summary of Change

Graphia v1.1 must now demonstrate Bedrock AgentCore end-to-end — Runtime, Gateway, Memory, and Observability — provisioned via an included infrastructure-as-code package, with local mode retained as a first-class run path for game-mechanics development. AI players gain in-game tool-use during the Day phase (an investigation tool that surfaces a target's prior public statements and vote record; an evidence-builder tool that compiles a structured case from logs), and the Moderator uses tools for mechanical work (kill-log summary, diary fetch, recap assembly). Per-player diaries persist via AgentCore Memory in remote mode and in the game's own state in local mode. The player chooses local or remote at launch.

---

## 3. Driver (Why This Change?)

**Primary driver (pick one):**

- [x] **User / stakeholder feedback**
- [ ] **Implementation learnings** — something discovered while building that invalidated an earlier assumption
- [ ] **New external constraint** — regulatory, vendor change, deprecation, cost, deadline
- [ ] **Strategic pivot** — product-level direction change
- [ ] **Error correction** — the earlier decision was wrong on its own terms
- [ ] **Scope adjustment** — descope or rescope based on capacity / priority
- [ ] **Other:** [describe]

**What was the previously-agreed assumption?** Graphia v1 was a LangGraph-only, local-only console reference with no cloud deployment in v1 — Bedrock AgentCore deployment was deferred to a future Phase 2 "Hosted Deployment" item, framed as optional, and AI tool-use was nowhere in scope.

**What changed about that assumption?** AgentCore deployment is now a hard requirement of the v1.1 product (with Terraform packaging, expanded to Runtime + Gateway + Memory + Observability), and AI tool-use becomes a core in-scope feature; local mode is retained explicitly as the second run path so game-mechanics development stays unblocked.

**Detailed reasoning:**

- **Stakeholder:** the project stakeholder. She advised expanding the reference to demonstrate **Bedrock AgentCore** and **LangGraph tool-use**, and *mentioned* AgentCore Memory in passing without making it a hard ask.
- **Stated / inferred motivation:** Primary motivation is making sure the author has demonstrable, hands-on skills in these patterns. Secondary motivation (inferred, not directly stated): being able to showcase that capability to other internal practice stakeholders. Possible third-order effects — establishing wider practice / potential-customer visibility, spawning tutorials or conference talks — are hypothetical and not relied on for this CR.
- **Authoring decision on AgentCore Memory:** the project stakeholder mentioned Memory but did not require it. The author elected to keep Memory in scope for v1.1 anyway, so the AgentCore demonstration is end-to-end (Runtime + Gateway + Memory + Observability) rather than partial. If implementation cost runs over, Memory is the most defensible item to descope back into local-state-only without invalidating the project stakeholder's original ask.
- **Guardrails deliberately out of scope:** Bedrock Guardrails was not part of the project stakeholder's ask; it slipped in earlier via a bundled "Observability + Guardrails" option in the product-definition interview and was removed once the author noticed. Guardrails can be revisited in a future CR if a security or compliance need surfaces.
- **Why now, vs. continuing with the LangGraph-only local-only skeleton:** Continuing as-is would leave the two patterns most likely to be needed in real engagements — managed agent runtime hosting and structured AI tool-calling — undemonstrated. The retained local mode preserves the existing skeleton's value as a fast inner-loop for game-mechanics work and as a no-AWS development path, so this CR expands the product without invalidating the work already done.

**Could this have been anticipated earlier?** Partially. The roadmap already listed "Bedrock AgentCore Deployment" as a future Phase 2 item, so the deployment direction itself was foreseen. What was not anticipated: its promotion to a v1.1 hard requirement, the AgentCore breadth (Gateway + Memory + Observability, not just Runtime), Terraform packaging, and the entire AI tool-use feature surface.

---

## 4. Nature of Change

- [ ] **Additive** — adds new behaviour without altering old (rare reason for a CR; usually a fresh spec covers this instead)
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope** — withdraws a previously-agreed requirement

The local-only-no-cloud-no-tools posture is overridden. The tool-use additions ride along but the headline change overrides previously-agreed scope.

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section)                                                  | What changes                                                                                                                                                                        | Already implemented? |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `context/product/product-definition.md §1.1, §1.3, §1.4, §2.1, §2.2, §3.1, §3.2`    | Vision, personas, success metrics, core features, user journey, in-scope, and out-of-scope all expanded to cover AgentCore + AI tool-use; version bumped to 1.1                     | Yes (this update)    |
| `context/product/roadmap.md` — Phase 2 "Hosted Deployment / Bedrock AgentCore Deployment" | Promoted from an optional future Phase 2 item to v1.1 hard scope; widened from Runtime-only to the full AgentCore demonstration set (Runtime + Gateway + Memory + Observability) provisioned via infrastructure-as-code | No                   |
| `context/product/roadmap.md` — net-new "AI Tool-Use" capability                     | New roadmap item: Day-phase investigation tool, evidence-builder tool, and Moderator helper tools become a core in-scope capability                                                 | No                   |
| `context/spec/001-playable-skeleton/` (functional-spec, tasks)                      | No acceptance criterion is invalidated; the completed skeleton is **reframed** as the local-mode baseline of a now-dual-mode product                                                | Yes (65/65 [x])      |
| `context/product/architecture.md`                                                   | Needs to be revisited to record the local-vs-remote run-mode duality, AgentCore as the deployment target, the Gateway-fronted tool surface, and AgentCore Memory as the diary store | Yes (exists; predates this CR) |

**Rework / migration required (any "Yes" or "Partially" above):**

- The completed v1 skeleton is *reframed*, not invalidated: its acceptance criteria continue to hold as the local-mode baseline. No `[x]` items are unmarked, and no rework of game mechanics is implied.
- New acceptance criteria are needed for the remote-mode capability (a full game playable against the deployed AgentCore Runtime) and for the AI tool-use capability (tool calls visibly inform Day-phase decisions). These new criteria belong in fresh specs produced via `/awos:spec`, not in spec 001.
- The architecture doc moves from describing a single-mode local product to describing a dual-mode product whose deployed mode is the v1.1 success target.

---

## 6. Decision

- **Decision:** Accepted. Phase 2 (Hosted AgentCore Deployment) was authored, executed across eleven slices, and verified Completed on 2026-05-20 — the scope change defined here is fully in effect.
- **Decided by:** _[pending]_
- **Decided on:** _[pending]_
- **Rationale:** _[pending — leave for reviewer/future-self after spec/tasks fall out]_

---

## 7. Follow-up Actions

- [ ] Run `/awos:roadmap` to rescope the "Hosted Deployment" item (promote to v1.1 hard scope; widen to the full AgentCore demonstration set) and add a new "AI Tool-Use" capability item.
- [ ] Run `/awos:architecture` to capture the local-vs-remote run-mode duality, AgentCore as the deployment target, the Gateway-fronted tool surface, and AgentCore Memory as the diary store.
- [ ] Run `/awos:spec` once the roadmap and architecture docs are updated, to draft fresh specs for the AgentCore deployment, the AI tool-use capability, and the AgentCore Memory diary store. Engineering-level decomposition (interface seams, refactors, test plans) belongs in those specs' technical-considerations and tasks files — not in this CR.
- [ ] Treat spec 001 as untouched: its acceptance criteria stand for local mode and no items are unmarked.
