# Graphia — Backlog

Open work not yet specced or scheduled, captured so it isn't lost between sessions.
Roadmap *features* live in [`product/roadmap.md`](product/roadmap.md); this file holds
**follow-ups, robustness gaps, measurement ideas, docs debt, and housekeeping** —
things surfaced during implementation that don't (yet) have a spec.

_Last updated: 2026-06-16._

---

## Follow-up specs — AI quality (evidence-driven, measured against the eval ledger)

- **Repetition reduction** — the top unsolved AI-quality problem. AI Day-speech repetition sits at **~0.36–0.45 (name-masked near-dup) on ollama** across the n=20 baseline, the post-013 run, *and* a live 4+1 game — untouched by the spec-013 grounding fix or the lineup. Spec 009's single anti-parrot prompt line is plainly insufficient. A dedicated fix (stronger anti-parrot prompt / context handling / temperature), A/B'd via `make repetition-experiment` + `make blunder-eval` against the recorded baseline, under the **effort-not-results** acceptance principle ([CR 005](change-requests/005-ai-behaviour-acceptance-effort-not-results.md)). _Origin: spec 011 baseline + live play 2026-06-16._
- **Nova Day-passivity** — the cloud model never initiates a Day vote (`vote_activity by_side {0,0}`), so its Day phase is silent and the town can't win. Spec 013's prompt nudge was **refuted** on Nova. Next attempt: a mechanical fallback (force a vote when the Day goes quiet, e.g. auto-nominate the most-discussed player) — a Day-*flow* change. _Origin: spec 013, refuted hypothesis._
- **Town-coordination / Day-decisiveness** — the town wins **0/20** on both providers even when individual votes are coherent (qwen post-013): coherent per-vote behaviour ≠ town coordination. Likely its own spec (richer Day strategy / decisiveness). _Origin: spec 013, open result._

## Roadmap features (in `roadmap.md`, not yet specced)

- **Multi-Round Mafia Consensus by Pointing** — the sibling Phase 5 item: Mafiosos converge on a victim over multiple rounds of private pointing, falling back to single-round majority + random tie-break within a cap. More meaningful now that lineups (hence Mafia counts) are configurable (spec 014). _Next: `/awos:spec`._
- **Phase 6** (Personas, Async Day Chat, End-of-Game Payoff) and **Phase 7** (Tool-Use & Expanded Roles) — each its own spec when reached.

## Robustness gaps

- **Graceful career-greeting degradation** — a transient stats-store failure (e.g. expired SSO) currently **crashes the game at boot**: `render_greeting(store.load())` in the UI driver propagates `UnauthorizedSSOTokenError` and the game exits before starting. The greeting should degrade gracefully ("career stats unavailable") rather than take down startup. Same class as the spec-010/011 cloud-at-boot issue. _Origin: live smoke 2026-06-16._

## Measurement / eval ideas

- **Lexical → semantic repetition metric** — the `repetition` metric is lexical (difflib ≥0.85 on name-masked text); it may **undercount player-perceived repetition** when the same point is reworded (semantic, not near-verbatim). A future refinement (embedding similarity) if we want the metric to track perception. Deliberate cheap-deterministic choice for now (spec 009/011). _Origin: live observation vs measured 0.36._
- **Repetition-vs-lineup sweep** — spec 014 made the lineup a *recorded, controllable* eval variable (`settings.lineup` + `--citizens/--mafia`). A quick 4+1 read (~0.36) sits in the 5+2 band, so lineup doesn't obviously move repetition — but a proper sweep could confirm, and a repetition A/B should pin the lineup. _Origin: spec 014._

## Docs debt

- **Eval Ledger Viewer tutorial** — spec 012 is Completed but un-tutorialised; the `012` tutorial slot is intentionally left open for it (`/buddah:tutorial 012`).
- **Spec 014 tutorial** — after verify (`/buddah:tutorial 014`).
- **Tutorials index** (`tutorials/README.md`) — missing rows for 011, 013, 014 (and the 012-gap note).

## Housekeeping

- **`product-definition.md` "future Ollama provider" wording** — the Ollama provider shipped (spec 010) but the product definition still calls it "future / on the roadmap." Minor wording refresh.

_(Resolved 2026-06-18: the parked AWOS adr/change-request/tutorial command renames were removed entirely — superseded by the buddah plugin — and the stale `handoff.md` transcript was deleted, de-dirtying the working tree.)_
