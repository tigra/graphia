# Tasks: Same-Round Message Visibility (Spec 008)

Vertical slices for [spec 008](./functional-spec.md) per its
[technical-considerations](./technical-considerations.md). Each slice leaves the
app runnable; verification is the offline `uv run pytest` suite (including
Textual `App.run_test()`), consistent with the project's testing posture — the
TUI needs a real terminal, so slice verification is automated tests, not a
manual app run. Agents: `langgraph-agentic` (the node module), `testing` (suite).

- [x] **Slice 1: AI speakers see the full current round (recent-discussion window 10 → 30)**
  - [x] Change the module constant `_CONTEXT_WINDOW` in `src/graphia/nodes/day.py` from `10` to `30`. Plain constant — no env var / `GraphiaConfig` surface. No other production change: `_render_context` already slices `messages[-_CONTEXT_WINDOW:]` and is the single context feed for both the day-speak prompt (`_ai_day_action` → `DAY_SPEAK_USER_TEMPLATE`) and the vote prompt (`_ai_ballot` → `AI_VOTE_USER_TEMPLATE`), so both AI views widen together (functional spec §3 In-Scope). **[Agent: langgraph-agentic]**
  - [x] Add a chokepoint unit test for `_render_context`: build a synthetic full round — one day-open `SystemMessage` plus seven `AIMessage`s with distinct `name=` values — and assert every speaker's line, *including the earliest*, appears in the rendered context (proves a full round fits in the window). Add a **guard assertion** that `_CONTEXT_WINDOW` is at least a full round's message count for the standard 7-player lineup, so a future shrink that would drop a round's earliest speaker fails loudly. Also assert an earlier speaker's line renders for a later speaker (functional spec §2.1). **[Agent: testing]**
  - [x] Run `uv run pytest -q`; confirm the new test passes and the existing suite stays green. **[Agent: testing]**

- [x] **Slice 2: The human sees the round's earlier messages on screen before speaking**
  - [x] Add a Textual `App.run_test()` test: seat AI speakers ahead of the human (monkeypatch `graphia.nodes.day._shuffle_order` so the human is at a later position; supply the AI speeches via `fake_sonnet(day_actions=[…])`), advance to the human's turn, and assert the `#public-log` text (via the existing `_rich_log_text()` helper) contains the earlier AI speakers' messages *before* the human speaks. No production code — the on-screen log is already unwindowed (the human's interrupt payload carries no discussion); the test locks the guarantee against regression (functional spec §2.1, §2.3). **[Agent: testing]**
  - [x] Run `uv run pytest -q`; confirm the UI test passes and the full suite is green. **[Agent: testing]**

---

_Out of scope (no task): asserting AI **behavior** when it sees earlier messages — only that the context is provided (functional spec §2.4; AI output is non-deterministic). No new on-screen control/setting and no change to turn flow, rounds, or vote rules — satisfied by construction since only one constant changes._
