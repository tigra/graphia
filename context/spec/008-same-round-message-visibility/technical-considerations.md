# Technical Specification: Same-Round Message Visibility

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

What a Day speaker can "see" is governed by a single chokepoint: `_render_context(messages)` in `src/graphia/nodes/day.py`, which renders the **last `_CONTEXT_WINDOW` messages** into the prompt context. Two facts from the codebase shape the work:

- **The AI's view is bounded** by `_CONTEXT_WINDOW` (currently `10`), and that same function is the sole context feed for **both** the AI day-speak prompt (`_ai_day_action` → `DAY_SPEAK_USER_TEMPLATE`) **and** the AI vote prompt (`_ai_ballot` → `AI_VOTE_USER_TEMPLATE`).
- **The human's view is not bounded.** The human's `day_turn` interrupt payload carries no discussion (only `kind`, `speaker_id`, `speaker_name`, `alive_names`, optional `error`); the human reads the on-screen `#public-log` (`RichLog`), which `_write_public()` in `src/graphia/ui/app.py` appends to and never truncates.

So the **only production change is to widen `_CONTEXT_WINDOW` from 10 to 30** so a full round comfortably fits in the AI's view. The human side already satisfies the spec and needs no code change. The remaining work is automated tests that lock the guarantee in.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Widen the recent-discussion window — **[Agent: langgraph-agentic]**

- Change the module constant `_CONTEXT_WINDOW` in `src/graphia/nodes/day.py` from `10` to `30`. **Plain module constant — no env var / `GraphiaConfig` surface** (the spec gives no reason to make it runtime-tunable; the project avoids speculative config).
- No other production change: `_render_context` already slices `messages[-_CONTEXT_WINDOW:]` and is the single feed for both the speaking and voting prompts, so both AI views widen together (functional spec §3 In-Scope explicitly includes the vote view).
- **Why 30:** with the fixed 7-player lineup a full speaking round is at most 7 speeches; adding the day-open announcement and any vote-announcement system messages keeps a round's worth of context well under 30. 30 leaves headroom for larger future lineups (Phase 5 configurable counts) while keeping the prompt bounded.

### 2.2 Human visibility — no production change — **[Agent: textual-tui]**

- Confirmed chokepoint: the human relies solely on `#public-log`, populated by `_write_public()` (`src/graphia/ui/app.py`), which appends every non-private message and is never windowed/truncated. The human therefore already sees all earlier same-round messages before their turn.
- This section is verified by a test (§2.3b), not by new production code.

### 2.3 Tests — **[Agent: testing]**

- **(a) Chokepoint unit test (primary):** exercise `_render_context` directly. Build a synthetic full round — one day-open `SystemMessage` plus seven `AIMessage`s with distinct `name=` values — call `_render_context`, and assert **every** speaker's line, *including the earliest*, appears in the rendered string. This proves the window covers a full round. Include a **guard assertion** that `_CONTEXT_WINDOW` is at least a full round's worth of messages for the standard lineup, so a future shrink that would drop a round's earliest speaker fails loudly.
- **(b) Human-view UI test:** one `App.run_test()` driving a Day where the human speaks at a **later** position (seat AI speakers ahead of the human by monkeypatching `graphia.nodes.day._shuffle_order`; supply the AI speeches via `fake_sonnet(day_actions=[…])`). Before the human's turn resolves, assert the `#public-log` text (via the existing `_rich_log_text()` helper) contains the earlier AI speakers' messages.
- **(c) AI later-position visibility (lightweight):** a `_render_context` assertion that an earlier speaker's line renders for a later speaker — largely covered by (a); kept explicit to map directly onto functional-spec §2.1.
- All tests are **offline** (no Bedrock/AWS) and follow existing suite patterns: `fake_sonnet`, the `_shuffle_order` monkeypatch for deterministic order, and the `App.run_test()` + `_rich_log_text()` / `_wait_for()` helpers. No new LLM call sites are introduced, so `safe_llm` needs no extension.

### 2.4 Explicitly not tested

- No automated test asserts AI *behavior* (what the model says when it sees earlier messages) — only that the context is **provided**. AI output is non-deterministic; the guarantee is about visibility, not reaction.

---

## 3. Impact and Risk Analysis

- **System dependencies:** `_render_context` is the only consumer of `_CONTEXT_WINDOW`. Widening it touches only the AI prompt-context size for day-speak and voting. No change to `GameState` shape, graph topology, reducers, the UI, or the human turn.
- **Risk — larger prompts:** each AI day/vote turn now renders up to 30 recent messages instead of 10. *Mitigation:* with ≤7 players the realistic message count rarely approaches 30; the extra tokens/latency are negligible, and more context generally improves both speech and vote quality.
- **Risk — vote-context change is intentional but broad:** an AI casting a ballot now sees up to 30 recent messages. This is in-scope per functional-spec §3 and is a net improvement to the vote decision; flagged here so it isn't a surprise.
- **Risk — silent future regression:** a later change could re-shrink the window and quietly lose round visibility. *Mitigation:* the guard assertion in §2.3a fails if `_CONTEXT_WINDOW` drops below a full round.
- **Determinism:** tests pin speaking order via the `_shuffle_order` monkeypatch (not RNG seeding), consistent with the project's determinism posture.

---

## 4. Testing Strategy

- **Unit (primary):** `_render_context` over a synthetic full round, plus the `_CONTEXT_WINDOW` ≥ full-round guard.
- **UI integration:** a single `App.run_test()` confirming the human sees earlier same-round messages in `#public-log` before speaking.
- All offline/mocked; runs under `uv run pytest`. The exact test file (new slice-named module vs. appended to the existing day test file) is decided at `/awos:tasks`.
