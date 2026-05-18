# Change Request: Revise §2.2 Launch Error Handling and the §2.1 Next-Step Hint

- **CR ID:** 004
- **Date:** 2026-05-18
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [ ] `context/product/product-definition.md` — section: [name]
- [ ] `context/product/roadmap.md` — phase / item: [name]
- [x] `context/spec/002-hosted-agentcore-deployment/functional-spec.md` — section: §2.1 (one-time setup) and §2.2 (launching in remote mode)
- [x] Other: surfaced by `/awos:verify` of spec 002 — verification found three acceptance criteria the implementation does not satisfy as written and that should be revised.

**Context:** `/awos:verify` of spec 002 flagged that acceptance criteria §2.2.4, §2.2.5, and §2.1.4 describe behaviour the delivered design intentionally handles differently; this CR revises those three.

---

## 2. Summary of Change

§2.2.4 and §2.2.5 — which required `--remote` to *refuse to start* with specific named messages when the SSO session is expired or no Runtime is reachable — are relaxed: those AgentCore auth/reachability failures may surface through the in-game failure modal (which already names the CloudWatch log group + a session filter) rather than a dedicated pre-launch refusal. §2.1.4's post-`terraform apply` "next step" hint is reframed to point the developer at `make play-remote` rather than the raw `uv run python -m graphia --remote` invocation.

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

**What was the previously-agreed assumption?** §2.2.4/§2.2.5 assumed Phase 2 would do dedicated *pre-launch* validation — detect an expired SSO session or an unreachable Runtime before the game starts and refuse with a specific named message — and §2.1.4 assumed the developer is pointed at the raw `uv run python -m graphia --remote` command after deploy.

**What changed about that assumption?** The delivered design surfaces AgentCore auth/reachability failures through the in-game failure modal rather than a pre-launch gate, and the project standardised on the Makefile as its task-runner, so the canonical play command is `make play-remote`.

**Detailed reasoning:** `/awos:verify` checked the functional spec against the implementation and found §2.2.4/§2.2.5 unmet — there is no pre-launch SSO or runtime-reachability check; an expired session or a stale/unreachable Runtime ARN instead produces an error mid-invocation that the Slice-8 failure modal catches and renders, naming the CloudWatch log group and a `thread_id` filter for the failed session. For a single-developer, single-region v1.x deployment this is adequate: the developer still gets a clear, actionable failure with the coordinates to investigate. A separate pre-launch refusal with an exact hardcoded message would be extra surface for marginal value, and the §2.2.4 wording itself cautions against hardcoding. Separately, §2.1.4's hint predates the build-tooling decision that made the repo-root Makefile the canonical task-runner; pointing a fresh contributor at `make play-remote` is consistent with every other workflow instruction in the project. The two genuinely-unmet functional criteria verification also found — §2.4.2 (gameplay diary read-back) and §2.4.5 (graceful diary-write fallback) — are *not* part of this CR: the spec is correct there and the implementation is simply incomplete; they are completed via a new task slice.

**Could this have been anticipated earlier?** Partly — §2.2.4/§2.2.5 were written before the failure modal existed (it arrived in Slice 8); once the modal was the chosen error surface, the pre-launch-refusal criteria were effectively superseded but not revised until verification caught the mismatch.

---

## 4. Nature of Change

- [ ] **Additive**
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope**

Relaxes §2.2.4/§2.2.5 and reframes §2.1.4. The underlying capability — the developer is told what failed and how to fix it; the developer is told how to start a remote game — is retained; only the delivery mechanism / suggested command changes.

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section) | What changes | Already implemented? |
| ---------------------------------- | ------------ | -------------------- |
| `context/spec/002-hosted-agentcore-deployment/functional-spec.md` §2.2 | Criteria 2.2.4 / 2.2.5 relaxed: `--remote` SSO-auth and Runtime-reachability failures may be surfaced via the in-game failure modal (log group + session filter) instead of a pre-launch refusal with a specific named message. | Partially — the failure modal is delivered (Slice 8); the pre-launch refusal is not, and per this CR will not be. |
| `context/spec/002-hosted-agentcore-deployment/functional-spec.md` §2.1 | Criterion 2.1.4's post-`terraform apply` next-step hint reframed to encourage `make play-remote` rather than the raw `uv run python -m graphia --remote` command. | No — the hint is not yet in the deploy output; it is added per the revised criterion. |
| `context/spec/002-hosted-agentcore-deployment/tasks.md` | A new task slice picks up the revised §2.1.4 hint alongside the unmet §2.4.2 / §2.4.5 criteria, so the revised spec can be re-verified. | No — new slice. |

**Rework / migration required:** No delivered work is invalidated — the failure modal stays as-is and now satisfies the relaxed §2.2.4/§2.2.5. §2.1.4 needs the next-step hint added to the deploy output (pointing at `make play-remote`); it is bundled into the new task slice. §2.4.2 / §2.4.5 are genuine implementation gaps handled by that same slice but out of this CR's scope.

---

## 6. Decision

- **Decision:** Accepted
- **Decided by:** Alexey Tigarev
- **Decided on:** 2026-05-18
- **Rationale:** The failure modal already gives the developer an actionable, well-located error for AgentCore auth/reachability failures — adequate for a single-developer v1.x — and `make play-remote` is the project's established launch contract. Revising the three criteria to match is cheaper and more honest than building pre-launch checks for marginal value.

---

## 7. Follow-up Actions

- [ ] Revise `functional-spec.md` §2.2.4, §2.2.5, §2.1.4 to the wording this CR accepts.
- [ ] Run `/awos:tasks` to create the new slice covering the revised §2.1.4 hint plus the unmet §2.4.2 / §2.4.5 criteria.
- [ ] Re-run `/awos:verify` for spec 002 once that slice is implemented.