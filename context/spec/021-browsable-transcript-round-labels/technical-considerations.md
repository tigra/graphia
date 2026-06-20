<!--
Technical considerations for spec 021 — Browsable-Transcript Round Labels.
Describes HOW the round-labeling fix is built, at an architectural level.
-->

# Technical Specification: Browsable-Transcript Round Labels

- **Functional Specification:** `context/spec/021-browsable-transcript-round-labels/functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

This is a **pure-renderer change, isolated to one function**. The preserved eval transcript is produced entirely by `render_transcript` in `src/graphia/tools/eval_transcript.py`, which walks a game's ordered `{node: delta}` event log (captured by `blunder_eval._play_one_game` from `graph.stream(stream_mode="updates")`) and emits the tagged document. The only code that needs to change is how that walker (`_render_phases`) splits a `<day>` section into `<round>` blocks.

**No engine change.** The Day engine already counts speaking rounds correctly: `nodes/day.py` `day_turn` bumps `day_rounds` (via `_round_complete_update`) on every completed round-robin pass, and a *failed* vote (`resolve_vote`) deliberately does **not** bump it. The bug is purely in the renderer, which today opens a new `<round>` only when a vote *fails* — so several real rounds (each a `day_rounds` bump) collapse into one labeled block. The fix re-keys round splitting onto the engine's own `day_rounds` signal.

Because `day_rounds` is the authoritative round counter, keying off it makes the transcript's "Round N" match the engine's true round number — and, critically, **equal the round number that sibling spec 020 (Game-Time in the Recap) encodes as the in-world clock *inside* each recap**. Once both specs ship, a `<round>` block and the recap it contains must agree on the round number, or the transcript contradicts itself. The `day_rounds`-keyed labeling here is what secures that agreement (see *Compatibility with sibling specs 019 & 020* in §2). Recap *wording* is otherwise reproduced as-is (functional-spec §2.3).

Affected files: `src/graphia/tools/eval_transcript.py` (logic + docstrings) and `tests/test_eval_transcript.py` (new round-splitting tests). `nodes/day.py`, `state.py`, `graph.py`, and the eval-ledger viewer are **untouched**.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Logic / Algorithm — round-boundary detection in `_render_phases`

The walker accumulates the current `<day>` as `day_header` (the day-open lines) plus `day_round_bodies` — a list of round bodies, one per speaking round. At `flush()` each body is wrapped as `<round>` with the label `Round {n}.` where `n` is `enumerate(start=1)`. The list is created fresh at each `day_open`, so **numbering already resets per Day** (functional-spec §2.1, *Each speaking round is its own labeled block*) — no change needed there.

The change is the **trigger** for opening a new round body:

| | Current (buggy) | New |
| --- | --- | --- |
| Opens a new round | `resolve_vote` delta with `day_votes_called` (a *failed* vote) | a `day_turn` delta that carries `day_rounds` (a completed round-robin pass) |
| Failed vote | opens a new round | **stays in the current round** (vote + result inside it) |
| Recap placement | wherever it streamed | closes the round it summarizes (unchanged — see below) |

Two refinements make the edge cases land correctly:

1. **Lazy open.** A `day_rounds` bump *ends* a round but does not necessarily *begin* a visible new one — the final round-cap wrap (`day_rounds == DAY_MAX_ROUNDS`) is followed by `day_close`, not another speech. So on a wrap we set a `pending_round_break` flag and defer opening the next body until the **next `day_turn`** actually arrives. If the day ends instead, no spurious empty round is created.

2. **`day_close` appends to the current round.** The day-ending content — the "Day ends with no one executed" line and/or the final recap (emitted by `day_close`), or the deciding-vote tally + execution reveal (emitted by `resolve_vote`) — always lands in the **last** round body and never opens a new one (functional-spec §2.5, *Day endings land in the final round*). Only a `day_turn` event consumes `pending_round_break`; `day_close` and the vote nodes (`vote_prompt` / `collect_votes` / `resolve_vote`) never do.

Per-`day_turn`-delta order of operations inside the day branch:

1. if `pending_round_break` and `node == "day_turn"`: append a fresh body, clear the flag;
2. append this delta's messages to `day_round_bodies[-1]`;
3. if `node == "day_turn"` and `"day_rounds" in delta`: set `pending_round_break`.

**Why `day_rounds`, not the recap message.** The end-of-round recap is gated by `recap_enabled` (spec 018, toggleable for ablation) and its text will change under spec 020. Keying on the recap would be fragile and would break when recaps are disabled. `day_rounds` is emitted on every wrap regardless of `recap_enabled`, so the round structure stays correct with recaps on or off.

**Recap placement is already correct once the break is re-keyed.** `_round_complete_update` appends the round's recap to the *same* `day_turn` delta's `messages`, after the speech. With the wrap delta appended to the round it closes (step 2 above, before the flag is set), the recap is the closing line of that round (functional-spec §2.3). The final round's recap arrives via `day_close` and lands in the last round by refinement 2.

### Component Breakdown — `src/graphia/tools/eval_transcript.py`

- **`_render_phases`** — replace the `_vote_failed` round-break with the `pending_round_break` / `day_rounds` logic above; initialize and reset the flag alongside `day_round_bodies` (at `day_open` and in `flush`).
- **`_vote_failed`** — **remove** (its only caller is the dropped branch).
- **Docstrings** — update the module header's "Structure" paragraph and `_render_phases`' docstring, which currently state "a fresh round opens whenever a vote *fails*", to describe the `day_rounds`-keyed boundary.
- **Defensive nicety** — in `flush`, omit a trailing **empty** round body so a degenerate all-dead `day_order` (which bumps `day_rounds` with no speech) cannot emit an empty `Round N.` block. Numbering stays contiguous because empties are dropped before `enumerate`.

### What does NOT change

- **Data model / state:** none. `day_rounds`, `day_votes_called`, `day_order` and their reducers (`state.py`) are unchanged; this spec only *reads* `day_rounds` from the already-streamed delta.
- **Engine nodes:** `nodes/day.py` round bookkeeping, recap emission, and routing are unchanged.
- **Transcript content:** speeches, votes, roles, persona block, Night "Pointing round N" labels, and recap wording are all reproduced exactly as today (functional-spec Out-of-Scope). Only Day round grouping/labeling changes.
- **Persistence / viewer:** `write_transcript` paths and the `make view-ledger` viewer are untouched — they consume whatever string the renderer returns.

### Compatibility with sibling specs 019 & 020

**Spec 019 (Recap-Aware AI Reasoning)** factors `_render_standings` and injects standings into the AI Day-speech/vote prompts — it touches `nodes/day.py` + `prompts.py` only. The transcript renderer reads neither prompts nor `_render_standings`. **No shared file, no interaction.**

**Spec 020 (Game-Time in the Recap)** is the binding one. Its tech spec assigns every recap a canonical round number — `new_rounds` for a round-wrap recap, and `ended_on_round = day_rounds if day_rounds >= DAY_MAX_ROUNDS else day_rounds + 1` for the `day_close` recap — and renders it as an in-world clock *inside the recap text*. That is the round number a reader sees in each recap. For the preserved transcript to be internally consistent, the `<round>` label that **contains** a recap must equal the round number that recap's clock encodes. The `day_rounds`-keyed labeling delivers exactly this: body #K closes when `day_rounds` reaches K, and the in-progress final pass is body #(`day_rounds`+1) — the very function spec 020 uses. Worked correspondence:

| Situation | 021 block label | 020 recap clock-round |
| --- | --- | --- |
| round 1 wrap | Round 1 | `new_rounds` = 1 → 9 AM |
| round 5 wrap | Round 5 | `new_rounds` = 5 → 9 PM |
| round-cap close (6 passes done) | Round 6 | `ended_on_round` = 6 → 12 AM |
| execution mid round 3 (2 passes done) | Round 3 | `ended_on_round` = `day_rounds`(2)+1 = 3 → 3 PM |
| **failed vote inside round 2, pass then completes** | **Round 2** (vote stays inside) | `new_rounds` = 2 → 12 PM |

The last row is **why a failed vote must not open a new round**: if it did, the restarted pass would be labeled "Round 3" while its closing recap's clock reads 12 PM (round 2) — a self-contradictory transcript. The recommended failed-vote handling is therefore a **compatibility requirement** with spec 020, not a stylistic preference; this also satisfies functional-spec §2.2 (*Each round block holds only that round's events*).

**Implementation order.** 019 and 020 are coupled (both refactor `render_day_round_recap`; 019 must land first). Spec 021 touches a **different** file (`eval_transcript.py`), never calls `render_day_round_recap`, and keys on the `day_rounds` channel in the streamed delta — not on the recap's text or signature. So 021 is **order-independent** relative to 019/020 and robust to 020's recap-text and `day_round`-parameter changes. A transcript captured before 020 ships simply carries clock-free recaps; the round labels are correct either way.

---

## 3. Impact and Risk Analysis

- **System Dependencies:** The fix relies on the LangGraph `updates` stream surfacing each node's return dict verbatim, and on `day_turn` returning `day_rounds` **only** on a round-robin wrap (via `_round_complete_update`). Both hold today; the renderer already depends on this same mechanism for Night channels (`night_rounds_log` / `night_round_picks`). If a future change made `day_turn` emit `day_rounds` on non-wrap steps, the round split would drift — noted so a reviewer keeps the wrap-only invariant.
- **Behavioral change vs. today:** A Day containing a *failed* vote previously rendered extra `<round>` blocks (one per failed vote); it now renders exactly one block per completed pass, with the failed vote inside its round. This is the intended correction, not a regression.
- **Already-committed transcripts:** unchanged — they are static text files rendered under the old logic; re-rendering them is explicitly out of scope (functional-spec). Only transcripts from future measured runs carry the new labels.
- **Determinism:** none affected. The renderer is pure and reads an existing event log — no RNG, no I/O — so it does not touch the determinism posture (architecture §6, *Determinism Posture & Testing Conventions*) and cannot affect the dual-mode byte-equal smoke test.
- **Potential Risks & Mitigations:**
  - *Spurious empty round at day end* → mitigated by lazy-open + dropping empty trailing bodies in `flush`.
  - *Recap landing in the wrong round* → mitigated by appending the wrap delta (which already carries the recap) to the closing round **before** setting `pending_round_break`.
  - *Misreading non-`day_turn` deltas as round starts* → mitigated by gating both the open and the flag-set on `node == "day_turn"`.

---

## 4. Testing Strategy

All tests are unit tests over **synthetic event logs**, extending `tests/test_eval_transcript.py` — the renderer's source of truth is the ordered event log, so a hand-built log is the correct, fully-deterministic substrate (matching the file's existing design and the all-mocked `pytest` suite; no real LLM, no `make` eval harness).

- **New multi-round Day helper:** build a `<day>` whose `day_turn` deltas carry `day_rounds` (1, 2, 3, …) with an attached recap `SystemMessage`, to simulate genuine round-robin wraps.
- **Assertions to add:**
  - A Day with N wraps renders N `<round>` blocks labeled `Round 1.` … `Round N.` (functional-spec §2.1).
  - Each round's recap is the **last line inside that round's block**, attributable to the correct round (functional-spec §2.3).
  - Numbering **resets per Day**: a second Day's first block is `Round 1.` (functional-spec §2.1).
  - A **failed** vote (`resolve_vote` delta carrying `day_votes_called`) does **not** open a new round — its tally + "vote fails" line stay in the current round (functional-spec §2.2).
  - **Day endings land in the last round:** an executed vote's reveal, or the "Day ends with no one executed" line + final recap, appear inside the final block with no empty block following (functional-spec §2.5).
  - **`recap_enabled=False` parity:** a Day whose wrap deltas carry `day_rounds` but no recap message still splits into the right number of `<round>` blocks (round structure is recap-independent).
- **Regression guard:** existing single-round-Day tests (executed-vote path, structural-tag, chronological-order, defensive-input cases) stay green — a Day with no `day_rounds` bumps still renders exactly one `Round 1.` block.
- **Tooling:** `uv run pytest tests/test_eval_transcript.py -q` for the focused loop; `uv run pytest -q` before completion.
