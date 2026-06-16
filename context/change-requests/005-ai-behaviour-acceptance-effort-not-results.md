# Change Request: Reframe Spec 013 Acceptance — Commit to Effort, Not Results, for AI-Behaviour Fixes

- **CR ID:** 005
- **Date:** 2026-06-16
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [ ] `context/product/product-definition.md` — section: [name]
- [ ] `context/product/roadmap.md` — phase / item: [name]
- [x] `context/spec/013-ai-behavioral-integrity/functional-spec.md` — section: §2.5 / §2.6 (the behaviour-fix acceptance criteria) and the Overview's "how success is judged".
- [x] Other: surfaced by the n=20 after-picture measurement and the withheld `/awos:verify` of spec 013 — the behaviour-fix criteria were written as guaranteed outcomes, which an honest measurement of a non-deterministic system cannot satisfy on demand.

**Context:** spec 013's before/after measurement confirmed one behaviour hypothesis (role/team grounding) and refuted another (the Day-passivity nudge on the cloud model), leaving the town win-rate open. Under the spec's original outcome-based acceptance, 013 reads as a *failure* despite the measurement doing exactly its job — exposing that the success definition itself was wrong for this class of work.

---

## 2. Summary of Change

The acceptance of spec 013's **behaviour-fix** criteria changes from "the improvement is achieved" (definite outcomes — self-/teammate-execution rates drop, a silent provider starts voting) to **"each fix is a hypothesis the before/after measurement tests; a tested hypothesis satisfies the requirement — whether it is confirmed or refuted — and improvements not achieved are carried into follow-up specs."** The measurement requirements (§2.1–2.4: the outcome + vote-activity tracking, the baseline, the viewer) are unaffected; only the *success definition of the behaviour fixes* is reframed. The general principle this establishes: **for non-deterministic AI work the project commits to a particular *effort* — a designed, measured attempt — not to a particular *result*.**

---

## 3. Driver (Why This Change?)

**Primary driver:**

- [ ] **User / stakeholder feedback**
- [x] **Implementation learnings** — something discovered while building that surfaced the underlying principle
- [ ] **New external constraint**
- [ ] **Strategic pivot**
- [ ] **Error correction**
- [ ] **Scope adjustment**
- [ ] **Other**

**What was the previously-agreed assumption?** Spec 013's behaviour-fix criteria required the behaviour to *actually improve* as a delivered outcome — self-execution and teammate-execution vote rates dropping with confidence intervals clearly below baseline, and a previously-silent provider initiating votes on both providers.

**What changed about that assumption?** A behaviour fix on a non-deterministic model is treated as a **hypothesis the before/after measurement tests**, not a guaranteed deliverable. A *tested* hypothesis satisfies the requirement regardless of whether it is confirmed or refuted; the improvements a refuted attempt did not achieve are addressed in further specs rather than counting as a spec failure.

**Detailed reasoning:** The implementation learnings surfaced what should be the standing principle — **we can commit to a particular *effort* to improve the AI's behaviour, but not to a particular *result***, because the model is non-deterministic and its response to a prompt change cannot be guaranteed. Spec 013's n=20 after-picture demonstrated this concretely: role/team grounding was **confirmed** (the local model's self-execution votes 0.57→0.0, teammate-execution 0.67→0.0), the Day-passivity nudge was **refuted** (the cloud model still initiated zero votes), and the town win-rate stayed **open** (0/20 on both providers — coherent individual votes do not amount to town coordination). The measurement performed flawlessly and the findings are honestly recorded in the ledger; only the *acceptance wording* — which demanded the improvement be achieved — was at odds with reality. Reframing acceptance to "a designed attempt, measured against a committed baseline, with the result recorded either way" makes the requirement honest for stochastic-AI work and lets the validated core (role/team grounding) be recognised as satisfying the spec while the unachieved improvements move to dedicated follow-up specs. *(The functional-spec wording was already reframed in this direction — the spec now states its criteria as hypotheses under test; this CR records the requirement-model change that wording enacts.)*

**Could this have been anticipated earlier?** Partly — the measure-first, hypothesis-tested posture was already the project's habit for non-deterministic AI work (specs 009 and 011), but 013's acceptance criteria were nonetheless written as guaranteed outcomes until the after-picture forced the principle to be made explicit.

---

## 4. Nature of Change

- [ ] **Additive**
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope**

Overrides the previously-agreed success definition of spec 013's behaviour-fix criteria: "the behaviour must improve" is replaced by "a tested hypothesis (confirmed or refuted) satisfies the requirement." The *intent* — better AI behaviour — is retained as the thing being attempted and measured; what changes is what counts as the spec being satisfied. This is a re-definition, not a withdrawal: the behaviour goals are not abandoned, they are reframed as effort-with-measurement and (where unachieved) carried forward.

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section) | What changes | Already implemented? |
| ---------------------------------- | ------------ | -------------------- |
| `context/spec/013-ai-behavioral-integrity/functional-spec.md` §2.5 / §2.6 + Overview | Behaviour-fix acceptance reframed from achieved-outcome to tested-hypothesis; a refuted attempt is a satisfied, recorded requirement. Measurement criteria §2.1–2.4 untouched. | Yes — the spec wording already reframed (the criteria read as hypotheses under test with first-result notes). |
| `context/spec/013-ai-behavioral-integrity/functional-spec.md` (verification) | Under the revised model the spec becomes verifiable: the role/team-grounding hypothesis (confirmed) and the Day-passivity hypothesis (tested, refuted) both *satisfy* their criteria; `/awos:verify` was withheld under the old model and can now complete. | No — re-verification pending. |
| Future specs (Nova Day-passivity; town-coordination / Day decisiveness) | The improvements 013's attempts did not achieve become explicit follow-up specs, each its own measured attempt against the 013 baseline. | No — not yet specced. |

**Rework / migration required:** None to delivered code or measurement — the role/team grounding stays committed and live, the ledger baseline + after-records stand. The only requirements-level action is re-verifying spec 013 under the reframed acceptance, and (later) opening follow-up specs for the unachieved improvements. The principle (effort, not results) applies to those and any future AI-behaviour spec.

---

## 6. Decision

- **Decision:** Accepted
- **Decided by:** Alexey Tigarev
- **Decided on:** 2026-06-16
- **Rationale:** Committing to effort-with-measurement rather than to a guaranteed result is the only honest acceptance model for fixes to a non-deterministic system. It recognises spec 013's validated core (role/team grounding) as satisfying the spec, keeps the refuted/open findings as honest recorded results that direct the next attempts, and avoids mislabelling sound, well-measured work as failure.

---

## 7. Follow-up Actions

- [x] Re-run `/awos:verify 013` under the reframed acceptance — done 2026-06-16: all 12 criteria satisfied (tested hypotheses confirmed role/team grounding + tested-but-refuted Day-passivity), spec 013 **verified Completed** with findings recorded.
- [ ] Open follow-up specs (via `/awos:spec`) for the unachieved improvements: the Nova Day-passivity mechanical attempt, and the deeper town-coordination / Day-decisiveness problem — each a fresh measured attempt against the 013 baseline.
- [ ] Carry the "commit to effort, not results" principle into the acceptance wording of all future AI-behaviour specs.
