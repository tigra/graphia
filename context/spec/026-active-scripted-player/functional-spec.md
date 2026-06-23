# Functional Specification: Active Scripted Player for Measured Runs

- **Roadmap Item:** Eval-measurement unblocker — realizes the backlog's **Active-human eval variant**, sharpened by the 024+025 n=5 transcript investigation. Relates to the **Town-coordination / Day-decisiveness** thread. Not a distinct roadmap phase item.
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

In measured (eval) runs there is no real person; the seat a human would occupy is filled by an **automated stand-in**. Today that stand-in is **passive** — it never proposes an execution and always votes *not* to execute. The 024+025 transcript investigation (run `2026-06-20T21-15-30`) showed this passive seat is now the **binding constraint on whether the AI town can ever win**, independent of how well the AI deduces:

> In a 5-Law-abiding + 2-Mafia game, after the first night the active table is 3 real-deducing Citizens + 1 passive stand-in + 2 Mafiosos, and an execution needs a **majority (4 of 6)**. The 3 Citizens can muster at most **3 Yes**, while the passive stand-in's reflexive No plus the two Mafiosos' self-No form a permanent **3-No block** — so a **correct** vote on a Mafioso stalls at **3–3 and fails**. In 2 of 5 games the AI town identified and repeatedly voted the *real* Mafia and still could not convict; the only votes that ever passed were citizens executing fellow citizens, which the Mafia amplified.

So a passive stand-in makes a town win arithmetically near-impossible — the eval can't measure whether the AI town (or the AI Mafia) actually plays well. This change gives the stand-in a **simple, transparent, deterministic rule-based strategy** so a measured game tests the AI players against a *competent-but-simple* opponent, and a correct town majority can actually form.

The stand-in plays differently by the role it is dealt:

- **As a Law-abiding Citizen**, it deduces from public events and votes to convict its top suspect (see §2). It is the missing competent town vote that can break the 3–3 deadlock.
- **As a Mafioso**, it protects its teammates and drives out the town's strongest hunters (see §2).

It is **deterministic** — its every choice follows fixed rules over the public game so far, with **no AI-model call** (free, reproducible). It is selectable against the current passive stand-in; the **active stand-in is the new default**, with passive available behind the flag to reproduce the prior baselines — so each measured run records which stand-in it used (the existing committed baselines are all passive; this is a deliberate, recorded baseline shift).

**Success looks like:** a measured game's stand-in plays a clear, deterministic strategy matched to its dealt role, states its reasoning so the transcript shows it, and — when Law-abiding — can supply the vote that lets a correct town majority convict; and a measured comparison records **whether** this lets the AI town's win-rate rise (and whether the AI Mafia can beat an active Law-abiding seat), confirmed or refuted under effort-not-results ([CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)).

---

## 2. Functional Requirements (The "What")

- **A measured run uses an active automated stand-in, selectable against the passive one.**
  - The stand-in that fills the human seat in measured runs can be either the **active** rule-based player (this spec) or the prior **passive** one (never proposes, always votes not-to-execute). The active stand-in is the **default**; passive is selectable to reproduce prior baselines. Each measured run **records which stand-in it used**, so records stay comparable across the change.
  - **Acceptance Criteria:**
    - [x] Given the default setting, when a measured run is played, then the stand-in plays the active strategy and the run's record states that the active stand-in was used.
    - [x] Given the passive setting selected, when a measured run is played, then the stand-in behaves exactly as before (never proposes a vote, always votes not-to-execute) and the run's record states that the passive stand-in was used.

- **As a Law-abiding Citizen, the stand-in deduces from public events and votes its top suspect.**
  - It keeps a **suspicion read of each living player** built only from public events: who proposed an execution, who voted which way, who was executed (and the side then revealed), and who was killed overnight (always a Law-abiding Citizen). Reading those against the sides that get confirmed over time: proposing or voting to execute someone **later confirmed Mafia** marks the actor **town-aligned** (proposing weighted more strongly than a follow-on vote); proposing or voting to execute someone **later confirmed Law-abiding** marks them **suspicious**; **sparing** (voting not-to-execute) a **confirmed Mafioso** marks them **suspicious**, while sparing a **confirmed Law-abiding** reads town-aligned; and when a player is killed overnight, whoever that victim had moved against becomes **more suspect** (the Mafia silence their hunters). A simple running score combines these.
  - It **states its key noted facts and current top suspicion aloud** in the discussion (so its reasoning is visible in the transcript).
  - In the **final discussion round of each Day**, it **proposes an execution vote against its highest-suspicion living player**. Whenever any execution vote is held, it **votes to execute when it suspects the target** (score past its bar) and to spare otherwise.
  - **Acceptance Criteria:**
    - [x] Given an execution that revealed a Mafioso, when the stand-in next reasons, then a player who *proposed* that execution is read more town-aligned than one who merely voted for it, and both more than one who voted to spare.
    - [x] Given a player who proposed or voted to execute someone later revealed Law-abiding, then the stand-in reads that player as more suspicious.
    - [x] Given a player killed overnight, then whoever that victim had earlier moved against gains suspicion.
    - [x] Given the final discussion round of a Day, then the stand-in proposes an execution vote on its highest-suspicion living player.
    - [x] Given an open execution vote, then the stand-in votes to execute a suspected target and to spare a trusted one.
    - [x] Given any turn, then the stand-in states its noted facts / top suspicion in the discussion.

- **As a Mafioso, the stand-in protects teammates and drives out the town's best hunters.**
  - Knowing its teammates, it **never proposes, night-targets, or votes to execute a teammate** (it always votes to spare a teammate) and works to **execute Law-abiding players** — choosing the Law-abiding player who most looks like an **effective Mafia-hunter** (the biggest threat to the Mafia's cover). Overnight it points the kill at that same threat; in the **final discussion round** it proposes that player's execution; and it **votes to execute any non-teammate** put up while sparing teammates. It voices suspicion of its chosen Law-abiding target and **never reveals that it is Mafia or who its teammates are**.
  - **Acceptance Criteria:**
    - [x] Given a teammate is put up for execution, then the Mafioso stand-in votes to spare.
    - [x] Given a non-teammate is put up for execution, then the Mafioso stand-in votes to execute.
    - [x] Given the final discussion round, then the Mafioso stand-in proposes executing its chosen Law-abiding threat; and overnight it points the kill at that same player.
    - [x] Given any of its turns, then the Mafioso stand-in never names a teammate, never proposes/night-targets/votes to execute a teammate, and never reveals its own side.

- **The active stand-in is fully determined by the public game so far (no AI model, reproducible).**
  - Its every choice follows fixed rules over the public events (and, for a Mafioso, its known teammates), so the **same game history yields the same choices every time**, and it makes **no AI-model call**.
  - **Acceptance Criteria:**
    - [x] Given the same game history, when the stand-in's turn is taken again, then it makes the same proposal/vote/night choice.
    - [x] Given a measured run, when the stand-in takes its turns, then no AI-model call is made on its behalf (no added token cost).

- **The choice of stand-in is an adjustable setting (ablatable).**
  - A setting selects active vs passive; the active stand-in is the default, and selecting passive reproduces the prior behavior for a side-by-side comparison (per the ablation-flag convention, [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)).
  - **Acceptance Criteria:**
    - [x] Given the setting at its default, when a measured run is played, then the active stand-in is used.
    - [x] Given the setting set to passive, when a measured run is played, then the passive stand-in is used (the prior baseline).

- **The effect on win-rates is measured, not assumed (effort-not-results).**
  - Whether an active Law-abiding stand-in lets the AI town actually win (breaking the deadlock where a correct vote previously stalled at 3–3), and whether the AI Mafia can beat an active Law-abiding seat, is an open question this change lets us test. A measured comparison against the passive baseline is run and recorded, confirmed or refuted.
  - **Acceptance Criteria:**
    - [x] Given a measured run with the active stand-in, when its outcomes are compared with the passive baseline (win-rate by side, share of correct vs own-goal executions, games resolved), then the comparison is recorded and the hypothesis logged as confirmed or refuted — either being a complete result.

---

## 3. Scope and Boundaries

### In-Scope

- An **active, deterministic, rule-based stand-in** that fills the human seat in **measured runs**, playing a role-matched simple strategy: a public-deduction-and-convict policy as a Law-abiding Citizen, and a protect-teammates-and-purge-hunters policy as a Mafioso.
- The stand-in **stating its noted facts / suspicion aloud** so its reasoning shows in the transcript.
- Making the stand-in **selectable** (active default, passive opt-in) and **recording which was used** with each measured run, so records stay comparable across the baseline shift.
- Measuring the effect on win-rate-by-side against the passive baseline, under effort-not-results.

### Out-of-Scope

- The **AI (LLM) players' behavior** — they remain model-driven and unchanged; this spec only changes the *stand-in* that fills the human seat in evals.
- **Real interactive play** — when a real person plays, they decide for themselves; the stand-in exists only in measured runs.
- Making the stand-in's strategy **optimal or LLM-driven** — it is deliberately a *simple* deterministic rule set, not a strong solver; a "blend-in / avoid-detection" Mafioso and richer scoring are possible later refinements.
- The AI town's own **force-a-vote / Day-decisiveness levers and own-goal prevention** (separate backlog items) — complementary, not part of this stand-in.
- Changing any **game rule, win condition, or vote mechanic**.
- Re-scoring games already recorded in the ledger.
- All other roadmap items, which are automatically out-of-scope for this specification.
