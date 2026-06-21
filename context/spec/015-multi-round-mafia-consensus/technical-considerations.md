# Technical Specification: Multi-Round Mafia Consensus by Pointing

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Today the Night kill is a single node, `mafia_pointing` (`src/graphia/nodes/night.py`), that collects one pick per living Mafioso in a single pass and an unconditional edge hands its `night_picks` to `resolve_night_kill`. This increment turns that into a **bounded multi-round loop** with three structural changes, all inside the existing Night phase — no new phase, no graph rewrite elsewhere:

1. **Per-pointer micro-step + conditional edge.** Replace the single-pass `mafia_pointing` with a self-looping node that handles **exactly one pointer per visit** (`mafia_point`), routed by a new `route_after_mafia_point` conditional edge — the same self-loop shape the Day phase already uses (`day_turn` → `route_day_turn_or_vote`). Each pick is committed as its own LangGraph super-step. This is the load-bearing choice: it gives within-round in-turn visibility for free (every visit reads all already-committed picks) **and** makes the human `interrupt()` replay-safe (a resume re-reads committed state and recomputes no prior AI pick — see §3). A single per-*round* node with a mid-loop interrupt would recompute the AI picks made before the human's turn on every resume, drifting from what the human was shown.

2. **Per-round random pointing order.** At the start of each round the living-Mafioso order is re-shuffled, reusing the Day fair-speaking-order pattern (`src/graphia/nodes/day.py::_shuffle_order`, module-global `random`) from [Spec 007 — Fair Day Speaking Order](../007-fair-day-speaking-order/functional-spec.md), so the last-to-point position is not systematically the same Mafioso (functional-spec §2.1).

3. **Resolution reads the deciding round.** `resolve_night_kill` keeps its existing tally-plurality-random-tiebreak logic and its career-event contract, but reads the **deciding round's** picks (the round that ended the loop — unanimous target, or the final round on cap) instead of a single `night_picks` dict.

The structured-output schema (`Pointing`) is unchanged; the AI pointing prompt and the human "point" interrupt payload are **extended** to carry the picks-so-far (by name) so both can converge. A few replace-style `GameState` channels track the round, the shuffled order, the cursor, and the rounds' picks, all reset in `night_open` alongside today's `night_picks = {}`.

**Primary stack: LangGraph orchestration** (`langgraph-agentic`) — topology, state, interrupt/replay, resolution, prompt. **Secondary: Textual UI** (`textual-tui`) — enriching the point modal with round + prior-picks context. **Testing** (`testing`) — deterministic multi-round trajectories. No new dependency; no new technology → no `/awos:hire`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Graph topology — per-pointer loop — `src/graphia/graph.py`, `src/graphia/nodes/night.py` — **[Agent: langgraph-agentic]**

Replace the node `mafia_pointing` (single pass) with `mafia_point` (one pointer per visit) and add a `route_after_mafia_point` conditional edge. Night edges become:

| From | To | Condition |
| --- | --- | --- |
| `night_open` | `mafia_point` \| `end_screen` | existing `route_after_night_open` (cycle-20 cap unchanged) — target renamed from `mafia_pointing` |
| `mafia_point` | `mafia_point` | more pointers remain in the current round |
| `mafia_point` | `mafia_point` | round complete, **not** unanimous, `night_round < 3` (start next round) |
| `mafia_point` | `resolve_night_kill` | round complete **and** (unanimous **or** `night_round == 3`) |
| `resolve_night_kill` | `check_win_night` | unchanged |

`mafia_point` responsibilities for one visit (mirrors `day_turn`'s reshuffle-on-wrap):

- **New round detection:** if `night_pointer_index == 0` and the round order is unset/empty, shuffle the living-Mafioso ids into `night_mafia_order` via the `_shuffle_order` pattern (§2.6) and reset `night_round_picks = {}`. (Pure/deterministic-given-RNG; no human-visible side effect.)
- **Identify the pointer** at `night_mafia_order[night_pointer_index]` (a pure read of committed state → replay-safe).
- **Human pointer:** `interrupt({"kind": "point", "options": [...], "round": night_round, "round_cap": 3, "prior_picks": <by-name summary>})` as the first effecting statement; the resume value is the chosen `target_id`. (On replay the node re-derives the same pointer from committed state and the interrupt replays its resume value — no AI recompute.)
- **AI pointer:** `_ai_pick_target(alive_law_abiding, prior_picks=...)` with the picks-so-far threaded into the prompt (§2.4); existing validation-retry-then-random-fallback preserved.
- **Commit:** record the pick into `night_round_picks[pointer_id]` and advance `night_pointer_index += 1`.

`route_after_mafia_point` (pure read-only router):
- `night_pointer_index < len(night_mafia_order)` → `mafia_point` (next pointer, same round).
- Else (round complete): append `night_round_picks` to `night_rounds_log`; then
  - **unanimous** (`len(set(night_round_picks.values())) == 1`) → `resolve_night_kill`;
  - else `night_round < 3` → bump round / reset cursor / clear order (so `mafia_point` reshuffles) → `mafia_point`;
  - else (`night_round == 3`) → `resolve_night_kill`.

  *(The per-round bookkeeping — appending to the log, incrementing `night_round`, resetting the cursor — is performed by a node, not the edge function; the edge only chooses the target. Concretely this is a tiny `mafia_round_advance` step on the not-unanimous-&-under-cap path, or folded into `mafia_point`'s new-round detection. Implementer's choice; the contract above is what matters.)*

**Lone Mafioso** needs no special case: `night_mafia_order` has length 1 → one pick → round complete → `set(...)` size 1 → unanimous → resolve (functional-spec §2.4).

### 2.2 State channels — `src/graphia/state.py` — **[Agent: langgraph-agentic]**

New fields on `GameState`, all **plain replace** reducers (matching today's `night_picks`) and all reset in `night_open`:

| Field | Type | Purpose |
| --- | --- | --- |
| `night_round` | `int` | current round, 1–3 |
| `night_mafia_order` | `list[str]` | the round's shuffled living-Mafioso ids (empty ⇒ reshuffle on next `mafia_point`) |
| `night_pointer_index` | `int` | cursor within `night_mafia_order` |
| `night_round_picks` | `dict[str, str]` | mafioso_id → target_id for the **current** round (the deciding round at resolution) |
| `night_rounds_log` | `list[dict[str, str]]` | completed rounds' pick dicts — context for the AI prompt / human payload, and the audit of how consensus was reached |

`night_open` already sets `night_picks = {}`; extend it to seed `night_round = 1`, `night_mafia_order = []`, `night_pointer_index = 0`, `night_round_picks = {}`, `night_rounds_log = []`. The legacy `night_picks` channel is superseded by `night_round_picks` for resolution; keep or drop it per the implementer (nothing else reads it once `resolve_night_kill` switches source).

### 2.3 Resolution & career stats — `resolve_night_kill` in `src/graphia/nodes/night.py` — **[Agent: langgraph-agentic]**

- Read the **deciding round's** picks from `night_round_picks` (the just-completed round at routing time) instead of `night_picks`. The tally/plurality/`random.choice`-tie-break body is **unchanged** — it is exactly today's single-round rule applied to the deciding round, which satisfies both the unanimous case (one distinct target) and the cap-fallback case (functional-spec §2.3).
- **Career event unchanged in contract** (`KIND_NIGHT_RESOLVED`, fields `victim_died`, `human_was_mafia_picker`, `human_picked_victim`): the human counts as a picker **once for the Night** (not per round), and `human_picked_victim` is true iff the human's pick **in the deciding round** equals the victim (functional-spec §2.5). The existing `human_night_attempts` / `human_night_successes` counters keep their meaning.
- The "no valid target / no picks" no-kill path and the morning announcement are untouched (functional-spec §2.5).

### 2.4 AI pointing prompt — `src/graphia/prompts.py`, `_ai_pick_target` in `src/graphia/nodes/night.py` — **[Agent: langgraph-agentic]**

- Extend `MAFIA_POINT_USER_TEMPLATE` with an **optional "teammates' picks so far" block** rendered **by name** — prior rounds from `night_rounds_log` plus the current round's picks-so-far from `night_round_picks` (e.g. `Round 1 — Alice→Carol, Bob→Dan; Round 2 so far — Alice→Carol`) — and an instruction that the team kills by **agreement**, so the model should move toward a shared target. The first pointer of round 1 gets an empty block ("no picks yet"). Keep the schema `Pointing.target_id` flat and unchanged; keep the retry-then-random-fallback.
- **Knowledge-boundary note (Spec 013 — AI Behavioral Integrity, `role-knowledge-boundary-invariant`):** disclosing teammates' picks is legitimate — these are Mafiosos, who already know one another from the first-Night intros; no Law-abiding player ever sees this block. Consistent with the existing grounding invariant.

### 2.5 Human point modal + driver — `src/graphia/ui/app.py`, `src/graphia/ui/widgets.py` (`PointingModal`), `src/graphia/driver.py` — **[Agent: textual-tui]**

- Extend the `{"kind": "point"}` interrupt payload with `round`, `round_cap`, and `prior_picks` (a by-name summary). The driver already forwards the payload verbatim; the app's `kind == "point"` branch passes the extras into `PointingModal`.
- `PointingModal` gains a short header ("Night kill — round 2 of 3") and a **read-only list of teammates' picks so far** (by name) above the existing target `OptionList`. The resume value is unchanged (the selected target's `id`); no new interrupt `kind`. Re-invocation across rounds already works today (the modal is round-agnostic) — this only enriches the display.

### 2.6 Reuse the fair-order helper — **[Agent: langgraph-agentic]**

Use the Day phase's shuffle helper rather than duplicating it: either import `src/graphia/nodes/day.py::_shuffle_order` or lift it into a tiny shared helper used by both Day and Night. One shuffle surface means **one monkeypatch point** for tests (architecture §6) and one place the determinism posture applies.

---

## 3. Impact and Risk Analysis

- **System dependencies / blast radius:** `graph.py` (night edges), `nodes/night.py` (`mafia_pointing` → `mafia_point` + router + `resolve_night_kill` source + prompt threading), `state.py` (5 new replace channels + `night_open` reset), `prompts.py` (`MAFIA_POINT_USER_TEMPLATE`), `ui/app.py` + `ui/widgets.py` (modal context), `driver.py` (payload pass-through). **No change** to the Day phase, endgame, win-condition (`check_win_night/day`), diaries, the career-event **contract**, or the eval/stats stores.

- **Risk — interrupt replay recomputes AI picks (the central one).** *Mitigation:* the per-pointer-step topology (§2.1) commits each pick as its own super-step, so a resume after the human's `interrupt()` re-reads committed picks and recomputes nothing — the reason per-pointer beats a single per-round node, whose AI-before-human picks would re-invoke the LLM on resume and drift from what the human was shown. This honors the project's "`interrupt()` as the first effecting statement" convention (CLAUDE.md) and mirrors the `day_turn` self-loop.

- **Risk — non-termination / unbounded rounds.** *Mitigation:* a hard cap of **3** enforced in `route_after_mafia_point`, beneath the existing cycle-20 safety cap in `night_open`.

- **Risk — new state channels drift / leak across Nights.** *Mitigation:* all are plain-replace and reset in `night_open` next to the existing `night_picks = {}`; no accumulating reducer to surprise a later Night.

- **Risk — the AI never converges.** *Accepted by design:* the cap-fallback always yields one victim. Convergence quality is non-deterministic AI behaviour (architecture §6) — a measured *effort*, not a guaranteed *result* ([CR 005 — effort-not-results acceptance](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)). A future "consensus-reached rate / rounds-to-agreement" eval metric is possible but **out of scope** (noted in `context/product/backlog.md`).

- **Risk — non-replayable per-round order & tie-break.** *Accepted* (architecture §6 — module-global `random`, no seed); tests pin both by monkeypatching the shuffle helper and the tie-break `random.choice`.

- **Determinism posture unchanged:** module-global `random` for the per-round order and the fallback tie-break; no `GRAPHIA_SEED`.

---

## 4. Testing Strategy — **[Agent: testing]**

Offline and model-free: drive the LLM boundary with the `fake_large` pointing fixtures and pin the order by monkeypatching the shuffle helper (§2.6) and `random.choice` for tie-breaks — the architecture §6 pattern, extending the existing night fixtures (`dynamic_night_pointing`, `target_human_pointing`).

- **Node / unit tests** (`tests/test_slice5_night.py` and a new multi-round file):
  - **Early agreement:** ≥2 AI Mafiosos, scripted unanimous round 1 → exactly one round, victim = agreed target, no extra LLM calls (assert pointing-call count).
  - **Convergence in round 2:** split round 1, unanimous round 2 → resolves at round 2; assert the round-2 AI prompt contains round-1 picks **by name** (the prompt-threading contract).
  - **Cap fallback:** split all 3 rounds → victim = majority of the **final** round; final-round tie → random tie-break (patch the tie selector).
  - **Lone Mafioso:** one living Mafioso → one pick → immediate resolve in one round.
  - **Per-round reshuffle:** patch the shuffle helper to record calls; assert it is called once per round (re-randomized each round, not fixed).
  - **In-turn visibility:** the Nth pointer's prompt / interrupt payload includes the prior pointers' picks **of the same round**.
  - **Replay safety:** a human-Mafioso trajectory through the **real driver** across multiple rounds; assert already-committed AI picks are **not** recomputed on resume (stable pointing-call count) and the human's shown `prior_picks` match the recorded picks.
  - **Career stats:** human counted as picker **once per Night** regardless of rounds; `human_picked_victim` iff the human's **deciding-round** pick == victim.
- **Integration (through the driver, like the existing night tests):** human-Mafioso full multi-round Night via `PointingModal` across rounds — assert the modal shows "round X of 3" + prior picks, the victim is announced, and a **non-Mafia human sees none** of the rounds.
- **Regression:** the default 2-Mafioso game still resolves; update the night tests for the new topology (the single-pass `mafia_pointing` is gone — the `resolve_night_kill` direct tests now feed `night_round_picks`; `dynamic_night_pointing` may need to supply picks across rounds). `safe_llm` is untouched (LLM calls stay in `nodes/night.py`).
- No new dependency; all tests offline against the mocked boundary.
