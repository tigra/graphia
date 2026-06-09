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
