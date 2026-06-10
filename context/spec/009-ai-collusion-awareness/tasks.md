# Tasks: AI Collusion Awareness (Spec 009)

Vertical slice for [spec 009](./functional-spec.md) per its
[technical-considerations](./technical-considerations.md). This is a
**prompt-only** increment: one light awareness sentence is appended to the AI's
Day **speaking** guidance (`DAY_SPEAK_SYSTEM`), and nothing else changes. Per
functional-spec §2.3 there is **no automated test** for the behaviour (AI output
is non-deterministic and the intent is emergent) — acceptance is **play-testing**
the running game, which the user does (the Textual TUI needs a real terminal).
The automated check is only a regression guard that the existing suite stays
green. Agents: `langgraph-agentic` (the AI day-speak prompt), `testing` (suite).

- [x] **Slice 1: AI Day speaking guidance names the copycat-collusion tell**
  - [x] Append the confirmed sentence — `Identical or near-identical messages from different players can hint at collusion.` — to the `DAY_SPEAK_SYSTEM` string in `src/graphia/prompts.py`, as a flat observation joining the existing "nervous, observant villager" framing (no imperative, no mandated reaction, no fixed call-out wording — functional-spec §2.1 light awareness, §2.2 emergent behaviour). **Do not** touch `AI_VOTE_SYSTEM` (vote prompt is out of scope — §2.1 third criterion, §3) or `DAY_SPEAK_USER_TEMPLATE`, and add no message-similarity detection code (§2.2, §3). **[Agent: langgraph-agentic]**
  - [x] Run `uv run pytest -q`; confirm the full suite stays green — the prompt edit must not break import/usage of `DAY_SPEAK_SYSTEM` or any day-node wiring. No new test is added (functional-spec §2.3). **[Agent: testing]**

---

_Acceptance is by play-testing only (functional-spec §2.3): run `uv run python -m graphia`, observe Day discussion, and confirm AI villagers occasionally treat near-identical messages with suspicion without forced or repetitive phrasing. No on-screen control, turn-flow, round, or vote-rule change (§2.3) — satisfied by construction since only the speak system prompt changes._

---

## Post-completion: dialogue-diversity regression (spans 008 + 009)

Play-testing surfaced the opposite of the §2.2 intent: instead of copycatting
becoming *risky and thus rarer*, AI Day dialogue collapsed into a **repetition
spiral** — players echoing each other (and then repeatedly *commenting on* the
repetition as collusion). A remote game measured **77% distinct / 56% near-dup**
speeches, with phrases repeated 3–4×. Likely driven by the interaction of spec
008 (context window 10→30, more to echo) and spec 009 (the nudge primes the
model to attend to — and reproduce — identical phrasing), on the Nova Pro
gameplay model. The model is **not** the new variable: Nova has been in place
since ADR-003; the recent specs are. **[Agent: testing]**

- [x] **Build a dialogue-diversity eval harness** — `make eval-dialogue` (`src/graphia/tools/eval_dialogue.py`): plays N seeded games on the REAL gameplay model with a scripted human, collects AI Day speeches, and scores lexical repetition (exact-dup, near-dup via difflib clustering, distinct ratio). Not a mocked pytest (hits live Bedrock); report-first with an optional `--min-distinct` gate.
- [x] **Validate + baseline on HEAD** — 2-game real-Nova run reproduces the issue: 43 speeches, 77% distinct, 56% near-dup, largest cluster 4 — matching the remote session's 77% distinct (the harness is faithful).
- [ ] **A/B to isolate the cause** — temporarily revert the two spec knobs (`_CONTEXT_WINDOW` 30→10 from 008; remove the 009 collusion line), re-measure with the same eval, and attribute the regression (008 vs 009 vs both).
- [ ] **Decide + apply the fix** from the A/B result — candidates: reword 009 toward anti-parroting ("say something new; don't restate others"), revert 009, and/or revisit the 008 window. Likely warrants a change-request since it revises a verified spec.

_Side finding (fixed in passing, not part of 009): the gameplay/mechanical model accessors were misnamed `get_sonnet`/`get_haiku` while actually bound to Nova Pro/Lite (ADR-003). Renamed to capability-tier `get_large`/`get_small` and corrected CLAUDE.md._
