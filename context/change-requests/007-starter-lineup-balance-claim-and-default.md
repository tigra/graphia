# Change Request: Correct the starter-lineup balance claim, and give the law-abiding side a margin

- **CR ID:** 007
- **Date:** 2026-09-03
- **Author:** Alexey Tigarev
- **Status:** Accepted

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [ ] `context/product/product-definition.md` — section: [name]
- [x] `context/product/roadmap.md` — phase / item: Phase 1 / **Fixed Starter Lineup**, and Phase 5 / **Configurable Role Counts**
- [ ] `context/spec/[NNN-slug]/functional-spec.md` — section: [name]
- [ ] Other: [describe]

**Context (1–2 sentences):** Phase 1's **Fixed Starter Lineup** item asserts that a lineup of 2 Mafiosos against 5 Law-abiding Citizens leaves "the game reasonably balanced toward the Law-abiding side". Phase 5's **Configurable Role Counts** made the counts player-configurable but left that same 5-and-2 standing as the default, so it is the item carrying the number this CR changes.

---

## 2. Summary of Change

The claim that the starter lineup is balanced toward the law-abiding side is withdrawn: it is not, and never was. The default lineup changes from **5 Law-abiding Citizens and 2 Mafiosos to 6 and 2**, so the town can survive one wrong execution instead of none. The counts remain player-configurable, so this changes only what a player gets if they choose nothing. No capability is added or removed.

---

## 3. Driver (Why This Change?)

**Primary driver (pick one):**

- [ ] **User / stakeholder feedback**
- [ ] **Implementation learnings** — something discovered while building that invalidated an earlier assumption
- [ ] **New external constraint** — regulatory, vendor change, deprecation, cost, deadline
- [ ] **Strategic pivot** — product-level direction change
- [x] **Error correction** — the earlier decision was wrong on its own terms
- [ ] **Scope adjustment** — descope or rescope based on capacity / priority
- [ ] **Other:** [describe]

**What was the previously-agreed assumption?** That a starter lineup of 5 Law-abiding Citizens against 2 Mafiosos leaves the game reasonably balanced in the law-abiding side's favour.

**What changed about that assumption?** Nothing changed — the assumption was simply false, and working out the arithmetic showed it: at that lineup the town can afford **zero** mistaken executions, and a side playing at random wins only **8.3%** of the time.

**Detailed reasoning:**

The lineup permits no margin whatsoever. One night kill takes a citizen; if the town then executes a citizen rather than a Mafioso, the next night's kill brings the two sides level and the game ends immediately — **the town never even reaches a second day to recover.** A single mistake on the first day is fatal, and the town must therefore be right about every execution, every time, to win at all.

That is not a "balanced" game in any ordinary sense, and the numbers bear it out. A town executing uniformly at random wins **8.3%** of games at this lineup. Measured against that baseline, every arm ever recorded in the quality ledger is statistically indistinguishable from random play:

| recorded arm | town wins | consistency with random play |
| --- | --- | --- |
| ollama, diaries on (reliable) | 2 of 10 | p = 0.20 |
| ollama, diaries off | 1 of 10 | p = 0.58 |
| ollama, diaries on (disqualified) | 0 of 10 | p = 1.00 |

**The AI town has never been shown to play better than chance.** At this lineup it would take 3 wins in 10 games to demonstrate otherwise, which no arm has produced. That reframes a standing question: the town's poor record has been read as a coordination and decisiveness failure — correctly, per [CR 006](006-reprioritize-phase-6-day-decisiveness.md) — but the lineup was also never giving it room to demonstrate anything.

Raising the citizen count by one restores a margin for a single error and roughly triples the random-play win rate, to about **22.9%**. Notably, **going further makes it worse before it makes it better**: 7-and-2 measures about 15.6%, below 6-and-2, because the margin only widens with every *second* citizen added while the odds of any given execution finding a Mafioso keep falling. 6-and-2 is the efficient choice, and "add more citizens" would have been the wrong reflex.

**Why the default and not just guidance.** The counts are already configurable, so a player who wants the harder game can still have it. But the default is what every new player meets, what every measured run uses, and what the roadmap's balance claim was about — so correcting the claim without correcting the default would leave the stated intent unmet.

**Could this have been anticipated earlier?** **Yes, and it partly was — which is the uncomfortable part.** Spec 026 (Active Scripted Player) recorded in June that at this very lineup a *correct* vote against a Mafioso stalled at 3–3 and failed, and that in 2 of 5 games "the AI town identified and repeatedly voted the *real* Mafia and still could not convict". That was diagnosed as a voting-threshold problem and fixed by making the passive seat participate. The diagnosis was right but incomplete: the same evidence was also saying the lineup left the town no room, and that half went unexamined for another two and a half months. The lesson is that "the town keeps losing" was treated as a question about AI behaviour for a long time before anyone asked whether the game was winnable.

---

## 4. Nature of Change

- [ ] **Additive** — adds new behaviour without altering old (rare reason for a CR; usually a fresh spec covers this instead)
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope** — withdraws a previously-agreed requirement

_Revisionary on two counts: it withdraws a stated balance property, and it changes an agreed default. Nothing is descoped — configurable counts keep their full range, including the old lineup._

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section) | What changes | Already implemented? |
| --- | --- | --- |
| `context/product/roadmap.md` — Phase 1, **Fixed Starter Lineup** | The "reasonably balanced toward the Law-abiding side" claim is withdrawn and replaced with an honest statement of the lineup's difficulty. The historical fact that the project shipped 5-and-2 is preserved. | Yes |
| `context/product/roadmap.md` — Phase 5, **Configurable Role Counts** | The default the item left standing changes from 5-and-2 to 6-and-2. The configurability itself is unchanged. | Yes |
| `evals/blunder-ledger.yaml` — the recorded series | ~~All 37 committed records were measured at 5-and-2~~ **— corrected 2026-09-03: that is false, and the error is worth keeping visible because it inverts this row's own advice.** Of the 25 records carrying a lineup, **20** are five-and-two; **four** (two n=20 pairs, 2026-06-21/22, both providers) were already played at **six-and-two** and are therefore directly comparable with post-change runs, and one was four-and-one. Twelve older records carry no lineup field at all. So the boundary is a **lineup** boundary, not a date one — the field on each record decides comparability. And town win rate is the primary outcome. A lineup boundary now runs through the project's longitudinal record: a 6-and-2 win rate is not comparable with a 5-and-2 one, because the game itself got easier. Every record already stamps its own lineup, so no record becomes wrong — only cross-boundary comparison becomes invalid. | Yes |
| `context/spec/001-playable-skeleton/functional-spec.md` — acceptance criteria and out-of-scope list | Names "2 Mafia and 5 Law-abiding Citizens" explicitly. **Recorded as affected-and-superseded rather than edited:** the spec is Completed and verified, and it is an accurate historical record of what was built. Its lineup is simply no longer the default. | Yes (Completed) |

**Rework / migration required:**

- **A fresh baseline is needed per provider at the new lineup, and it is deliberately deferred.** Re-baselining now would mean doing it twice, because the **Moderator Creative Recap** (Phase 6a, drafted as spec 040) is also still to land and will itself change what a completed game produces. The decision is to re-baseline **once**, after both this lineup change and the creative recap are in — and to treat the 5-and-2 records as a closed series until then.
- Until that re-baseline exists, any measured comparison must stay **within** one lineup. This includes spec 041's own A/B, which should therefore either complete before the lineup changes or run entirely after it.
- No data migration, no code migration beyond the default itself, and no change for any player or run that sets the counts explicitly.

---

## 6. Decision

- **Decision:** Accepted
- **Decided by:** Alexey Tigarev
- **Decided on:** 2026-09-03
- **Rationale:** The balance claim is false on its own terms and had to go regardless. Correcting the default alongside it costs nothing — the counts were already configurable — and is the smallest change that delivers the intent the claim was describing: a starter game in which the law-abiding side can be wrong once and still win. 6-and-2 specifically, because the arithmetic shows 7-and-2 is worse.

---

## 7. Follow-up Actions

- [x] Correct the Phase 1 **Fixed Starter Lineup** wording in `context/product/roadmap.md` so it no longer asserts a balance the arithmetic contradicts.
- [x] Change the default lineup to **6 Law-abiding Citizens and 2 Mafiosos**, leaving the counts configurable.
- [ ] Record the re-baseline boundary in the quality ledger, so nobody compares a 6-and-2 town win rate against a 5-and-2 one without seeing that the underlying game changed.
- [ ] Specify the **chance-baseline metric** as its own functional spec: record, per run, how the town's win rate compares with random play at that run's lineup (8.3% at 5-and-2, about 22.9% at 6-and-2), so "did the town beat chance?" becomes a falsifiable recorded result instead of a number nobody can interpret. This is the follow-up with the most measurement value, and it is what makes the deferred re-baseline readable when it happens.
- [ ] Re-run the per-provider baselines **once**, after both this change and the Moderator Creative Recap have landed.
- [ ] Update affected `functional-spec.md` / `technical-considerations.md` / `tasks.md` — none required; spec 001 is recorded as superseded rather than edited.
- [ ] Re-run `/awos:verify` for any spec whose acceptance criteria shifted — none.
- [ ] Update `context/product/architecture.md` — not required; the lineup is a product default, not an architectural assumption.
