# Graphia — Backlog

Open work not yet specced or scheduled, captured so it isn't lost between sessions.
Roadmap *features* live in [`product/roadmap.md`](product/roadmap.md); this file holds
**follow-ups, robustness gaps, measurement ideas, docs debt, and housekeeping** —
things surfaced during implementation that don't (yet) have a spec.

_Last updated: 2026-06-19._

---

## Follow-up specs — AI quality (evidence-driven, measured against the eval ledger)

- **Repetition reduction** — the top unsolved AI-quality problem. AI Day-speech repetition sits at **~0.36–0.45 (name-masked near-dup) on ollama** across the n=20 baseline, the post-013 run, *and* a live 4+1 game — untouched by the spec-013 grounding fix or the lineup. Spec 009's single anti-parrot prompt line is plainly insufficient. A dedicated fix (stronger anti-parrot prompt / context handling / temperature), A/B'd via `make repetition-experiment` + `make blunder-eval` against the recorded baseline, under the **effort-not-results** acceptance principle ([CR 005](change-requests/005-ai-behaviour-acceptance-effort-not-results.md)). _Origin: spec 011 baseline + live play 2026-06-16._
- **Nova Day-passivity** — the cloud model never initiates a Day vote (`vote_activity by_side {0,0}`), so its Day phase is silent and the town can't win. Spec 013's prompt nudge was **refuted** on Nova. Next attempt: a mechanical fallback (force a vote when the Day goes quiet, e.g. auto-nominate the most-discussed player) — a Day-*flow* change. _Origin: spec 013, refuted hypothesis._
- **Town-coordination / Day-decisiveness** — the town wins **0/20** on both providers even when individual votes are coherent (qwen post-013): coherent per-vote behaviour ≠ town coordination. Likely its own spec (richer Day strategy / decisiveness). _Origin: spec 013, open result._
- **Day-decisiveness levers (spec 018 n=10 review)** — the spec-018 day-round recap is accurate every round but **AI players never reason from it**; the town still wins **0/10** and **7/10 games stall to `no_winner`** (the model loops placid filler past the step budget without ever executing anyone). Candidate levers, each A/B-able under the effort-not-results principle: **(a)** feed the latest recap standings *directly into* the Day-speech/vote prompts (not just the scrolling context) so players act on "N town vs M mafia, we must execute today"; **(b)** **force-a-vote / Day-termination** as the Day nears the round cap (auto-nominate the most-discussed living player) — generalises the Nova force-vote idea above to qwen; **(c)** check & strengthen **rule-awareness / situation-recognition** in the prompts — do players actually grasp that executing the *living* (not eulogising the dead) is how the town wins, and where the game currently stands?; **(d)** bring forward the Phase-6 roadmap item **Per-AI Day-Round Private Thoughts** (a private between-rounds scratchpad) as a decisiveness aid — a place to form and commit to a read before speaking. _Origin: spec 018 n=10 ollama review 2026-06-19._
- **Persona realism & stress-reactivity (spec 018 n=10 review)** — generated personas collapse into one calm, naive "stay focused on the facts" voice; distinct setups (handyman, librarian) yield no distinct speech, and nobody escalates as neighbours die. Fixes: **(a)** strengthen persona salience in the Day-speech prompt (it is out-competed by a generic placid attractor) and/or add a persona-distinctiveness metric; **(b)** generate a wider temperament range — include **vigilant / suspicious / assertive** archetypes, not only calm ones; **(c)** define **behaviour under stress** — a night kill or an execution is high-stakes, and personas should react with urgency rather than platitudes; **(d)** stop **persona-backstory bleed** into game reasoning (a Mafioso named his legend's wife "Sarah" as a suspect) — backstory characters are flavour, not players at the table. _Origin: spec 018 n=10 ollama review 2026-06-19._

## Roadmap features (in `roadmap.md`, not yet specced)

- **Phase 6** (Async Day Chat, End-of-Game Payoff) and **Phase 7** (Tool-Use & Expanded Roles) — each its own spec when reached.

## Robustness gaps

- **Graceful career-greeting degradation** — a transient stats-store failure (e.g. expired SSO) currently **crashes the game at boot**: `render_greeting(store.load())` in the UI driver propagates `UnauthorizedSSOTokenError` and the game exits before starting. The greeting should degrade gracefully ("career stats unavailable") rather than take down startup. Same class as the spec-010/011 cloud-at-boot issue. _Origin: live smoke 2026-06-16._
- **AI self-name stutter in Day speech** — AI day-speech occasionally emits a leading self-name prefix, e.g. `Ivan: Ivan: I mean…` (3 such lines in the spec-018 n=10 run, which tripped the `third_person_self_talk` detector). Strip a leading `<own-name>:` prefix from AI speech output (defensive post-process at the speak site), or tighten the speak prompt/parse. _Origin: spec 018 n=10 ollama review 2026-06-19._

## Test reliability

- **Suite-wide ledger-write guard (belt-and-braces)** — a test-isolation bug let eval tests append ~25 synthetic records to the committed `evals/blunder-ledger.yaml`: `append_record`'s `ledger_path=LEDGER_PATH` was an **early-bound signature default**, so `run_eval`'s no-arg append always hit the real ledger and per-test `monkeypatch.setattr(LEDGER_PATH)` never reached it (**root cause fixed 2026-06-18** — the default is now `None`, resolved to the module global at call time). **Recommendation:** add an **autouse fixture pointing `blunder_eval.LEDGER_PATH` at `tmp_path`** for the whole suite, so no future eval test can touch the real ledger even if a redirect is forgotten; consider the same for `TRANSCRIPTS_ROOT`. _Origin: ledger pollution discovered 2026-06-18 during the spec-017 smoke._

## Measurement / eval ideas

- **Lexical → semantic repetition metric** — the `repetition` metric is lexical (difflib ≥0.85 on name-masked text); it may **undercount player-perceived repetition** when the same point is reworded (semantic, not near-verbatim). A future refinement (embedding similarity) if we want the metric to track perception. Deliberate cheap-deterministic choice for now (spec 009/011). _Origin: live observation vs measured 0.36._
- **Repetition-vs-lineup sweep** — spec 014 made the lineup a *recorded, controllable* eval variable (`settings.lineup` + `--citizens/--mafia`). A quick 4+1 read (~0.36) sits in the 5+2 band, so lineup doesn't obviously move repetition — but a proper sweep could confirm, and a repetition A/B should pin the lineup. _Origin: spec 014._
- **Engagement / decisiveness metrics (+ blunder-denominator caveat)** — the self/peer-vote blunder rates read **~0 only because the AI town barely votes** (spec-018 run denominators were 4 / 5 / **1**; Wilson CIs up to 0–79%), so a "clean" blunder rate can mask total non-engagement rather than signalling good play. Add engagement signals to the ledger/summary — **votes-initiated-per-game**, **% games resolved vs `no_winner`** — and surface them next to the blunder rates so the two are read together. _Origin: spec 018 n=10 ollama review 2026-06-19._
- **Active-human eval variant** — the scripted human always votes No and never initiates (already flagged in the outcomes `note`); this passive baseline likely suppresses vote-passing and inflates `no_winner`. Consider an opt-in **active-human** variant (sometimes votes Yes / initiates) as a second comparable measure, or surface the caveat more prominently in the summary. _Origin: spec 018 n=10 ollama review 2026-06-19._
- **Transcript round labels (spec 017)** — the transcript renderer labels round blocks by *vote-segments*, not engine speaking-rounds (one "Round 1." block can span several real rounds), which misled human reviewers twice during the spec-018 review and would mislead the Phase-7 LLM-as-Judge that reads these transcripts. Label each engine round (and/or annotate each Moderator recap with its true round number). _Origin: spec 018 n=10 ollama review 2026-06-19._

## Housekeeping

- **`product-definition.md` "future Ollama provider" wording** — the Ollama provider shipped (spec 010) but the product definition still calls it "future / on the roadmap." Minor wording refresh.
- **Closing-recap redundancy (spec 018)** — on a no-execution Day close, the existing "The Day ends with no one executed." line is immediately followed by the closing day-round recap's "No one has been executed today." — cosmetically redundant. Merge or suppress one for cleaner output. _Origin: spec 018 n=10 ollama review 2026-06-19._

_(Resolved 2026-06-18: the parked AWOS adr/change-request/tutorial command renames were removed entirely — superseded by the buddah plugin — and the stale `handoff.md` transcript was deleted, de-dirtying the working tree.)_
