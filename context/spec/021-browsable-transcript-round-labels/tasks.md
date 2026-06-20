# Tasks: Browsable-Transcript Round Labels (Spec 021)

One vertical slice — label each Day speaking-round in the preserved eval
transcripts with its true round number, keying off the engine's `day_rounds`
wrap signal. A **pure-renderer change** in `src/graphia/tools/eval_transcript.py`;
no engine / state / graph / UI change.

> **Implementation order:** independent of the coupled 019→020 pair. 021 touches
> a different file (`eval_transcript.py`), never calls `render_day_round_recap`,
> and keys on the `day_rounds` delta channel — not on recap text or signature —
> so it can land before, between, or after 019/020 and is robust to 020's
> recap-text/`day_round` changes. The one cross-spec invariant it must hold:
> a `<round>` label must equal the round number spec 020 stamps as the clock
> *inside* the recap that block contains (see tech-spec §2, *Compatibility with
> sibling specs 019 & 020*) — which the `day_rounds`-keyed labeling delivers.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: Preserved eval transcripts label each Day speaking-round by its true number**
  - [x] Re-key Day round splitting in `eval_transcript._render_phases` (per tech-spec §2, *Logic / Algorithm* + *Component Breakdown*): open a new `<round>` body on a `day_turn` delta that carries `day_rounds` (a genuine round-robin wrap), **lazily** — set a `pending_round_break` flag on the wrap and defer opening the next body until the next `day_turn`, so the final round-cap wrap **and** the `day_close` content (close line + final recap) land in the **last** round, not a spurious empty one. A **failed vote no longer opens a round** — remove the `_vote_failed` helper and its `resolve_vote` branch; the failed vote's tally/"vote fails" stay in the current round. `day_close` and the vote nodes (`vote_prompt`/`collect_votes`/`resolve_vote`) only ever append to the current round. In `flush`, omit an **empty trailing** round body (defensive: a degenerate all-dead `day_order` bumps `day_rounds` with no speech). Reset `pending_round_break` alongside `day_round_bodies` at `day_open` and in `flush`. Per-Day numbering already falls out of the per-Day `day_round_bodies` list + `enumerate(start=1)` — no change there. Update the module-header *Structure* paragraph and the `_render_phases` docstring (both currently say a round opens "whenever a vote *fails*") to describe the `day_rounds`-keyed boundary. **No `nodes/day.py` / `state.py` / `graph.py` change.** **[Agent: langgraph-agentic]**
  - [x] Add round-labeling tests to `tests/test_eval_transcript.py` (per tech-spec §4), over **synthetic** ordered event logs (the renderer's source of truth; all-mocked, no real LLM): a multi-round Day helper whose `day_turn` deltas carry `day_rounds` (1, 2, 3, …) with an attached recap `SystemMessage`. Assert: N wraps → N `<round>` blocks labeled `Round 1.` … `Round N.` (§2.1); each recap is the **last line inside the round it closes** (§2.3); numbering **resets per Day** — a second Day's first block is `Round 1.` (§2.1); a **failed** vote (`resolve_vote` delta carrying `day_votes_called`) does **not** open a new round, its tally/"vote fails" staying in the current block (§2.2); **day endings land in the last block** — an executed-vote reveal, or the "Day ends with no one executed" line + final recap, appear inside the final block with **no empty block following** (§2.5); and **`recap_enabled=False` parity** — wrap deltas carrying `day_rounds` but no recap still split into the right number of `<round>` blocks. Keep the existing single-round-Day tests (executed-vote path, structural-tag, chronological-order, defensive-input) green. **[Agent: testing]**
  - [x] Verification: `uv run pytest tests/test_eval_transcript.py -q` then the full `uv run pytest -q` green (incl. the byte-equal `test_dual_mode_smoke.py`, which a pure-renderer change cannot affect); render one synthetic multi-round Day (incl. a failed vote and a six-round cap) and eyeball that the `Round N.` labels are contiguous per Day and each recap sits in the round it closes. **[Agent: testing]** *(After this slice: future measured eval runs write transcripts whose Day round labels match the game's true round structure; normal gameplay and all other outputs are untouched.)*
