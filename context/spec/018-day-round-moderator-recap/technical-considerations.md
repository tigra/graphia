# Technical Specification: End-of-Round Day-Dynamics Recap

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

This is a **pure local-LangGraph change** — no AgentCore, no new graph nodes, no topology change, no new LLM call site. The recap is a deterministic, template-rendered **public `SystemMessage`** in the Moderator's voice, computed from existing game state and posted at Day round boundaries.

Because the recap is a public `SystemMessage`, two things come for free with no extra plumbing: it renders in the human UI like every other Moderator line, and `_render_context` (in `src/graphia/nodes/day.py`) already folds public Moderator `SystemMessage`s into each AI player's scrolling context window in chronological order — so it "informs their later speech and votes like an utterance" exactly as the functional spec requires.

The work is four small, well-contained additions: a pure renderer, one new state counter, one config flag, and the wiring to post the recap at the right boundaries (gated by the flag). The off-switch makes the recap an ablatable variable for a future study.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Architecture Changes

None structural. Same graph topology, same nodes, same edges. The change is additive: a new prompt constant, a new state field, a new config field, a pure helper, and recap-posting inside two existing Day nodes gated by the flag.

### State / Data Model Changes (`src/graphia/state.py`)

| Field | Type | Reducer | Purpose |
|---|---|---|---|
| `day_votes_initiated` | `int` | replace | Count of execution votes **called** so far this Day (any initiator, regardless of pass/fail). Reset to `0` in `day_open`; incremented by 1 at **both** vote-initiation sites in `day_turn` (the human `/vote` branch and the AI `DayAction(kind="vote")` branch). |

**Why a new counter (not derived):** the existing `day_votes_called` counts **failed** votes only (a successful vote returns from `resolve_vote` without bumping it), and AI-initiated votes increment no per-Day counter today. So no existing field answers "how many votes were called today" — `day_votes_called + (1 if executed else 0)` is provably wrong. Counting at **initiation** is deliberate: the recap reports a vote as "called" even when it's the move that ends the Day.

### Configuration (`src/graphia/config.py`)

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| `GraphiaConfig.day_round_recap_enabled: bool` | `GRAPHIA_DAY_ROUND_RECAP` | **on** | The ablation off-switch. When off, no recap is posted anywhere; the Day plays exactly as before. |

The existing `_env_truthy` helper is default-**off** (returns `False` when unset), wrong for this flag. Add a small default-aware helper, e.g. `_env_flag(name, *, default) -> bool` (returns `default` when unset/blank, else membership in the existing `_TRUTHY` set). Parse `day_round_recap_enabled = _env_flag("GRAPHIA_DAY_ROUND_RECAP", default=True)` in `load_config()`; give the dataclass field `= True` so direct-construction tests stay valid (consistent with `llm_provider` / `num_citizens`).

### Component Breakdown (`src/graphia/nodes/day.py`, `src/graphia/prompts.py`, `src/graphia/graph.py`)

- **`render_day_round_recap(state) -> SystemMessage`** *(new, pure, in `day.py`)* — reads `cycle` (day number), counts alive players by role from `players`, reads `day_votes_initiated`, and uses a shared `_executed_this_cycle(kill_log, cycle)` predicate to build the executed-today clause (name + revealed side via `_role_label`, or the no-execution variant). No mutation, **no RNG** (keeps the dual-mode smoke byte-equal). Returns a public `SystemMessage` (no `private_to`).
- **`_executed_this_cycle(kill_log, cycle)`** *(new, lift from the inline predicate already in `day_close`)* — DRY: shared by `day_close` and `render_day_round_recap`.
- **`_round_complete_update(state, rounds, *, recap_enabled, extra=None)`** *(new helper)* — all **three** round-wrap return sites in `day_turn` (normal speak/vote path + the two defensive dead-player/empty-order paths) funnel through it. It bumps `day_rounds`, reshuffles `day_order`, resets the index, merges `extra`, and appends the recap **iff `recap_enabled AND new_rounds < DAY_MAX_ROUNDS`**.
- **`day_open`** — add `day_votes_initiated: 0` to its reset block.
- **`day_turn`** — gains `recap_enabled: bool = True`; increments `day_votes_initiated` at both initiation sites; routes its wrap returns through `_round_complete_update`.
- **`day_close`** — gains `recap_enabled: bool = True`; appends `render_day_round_recap(state)` after its existing close-line logic when `recap_enabled`. (It is reached only when the Day ends *and the game continues* — the win path routes to `end_screen`, bypassing `day_close`, so the recap is never posted on a game-ending move.)
- **`graph.py::_assemble_graph`** — gains a `recap_enabled: bool` parameter; binds it into `day_turn` and `day_close` via `partial` (alongside the existing `career_emitter`/`game_id` injection). **Both** `build_graph` (passes `config.day_round_recap_enabled`) **and** `runtime/graph_builder.py::build_runtime_graph` (gains a `day_round_recap_enabled` parameter, passed from config by the Runtime entrypoint) must thread it, so local and remote can't drift.

### Prompt Template (`src/graphia/prompts.py`)

A new `DAY_ROUND_RECAP_TEMPLATE` constant, brief and present-tense, consistent with the existing `DAY_OPEN_*` / `VOTE_*` constants. The conditional executed-clause is assembled in `render_day_round_recap` and passed in as a finished string (same pattern as `team_line` / `relationship`). Representative output:

- No execution, no votes: `"Day 1 status: 5 Law-abiding Citizens and 2 Mafiosos remain. No execution votes called yet today. No one has been executed today."`
- After an execution: `"Day 2 status: 4 Law-abiding Citizens and 1 Mafioso remain. 1 execution vote called today. Dana was executed today and was revealed to be Mafia."`

(Singular/plural handled for the two role counts and the vote count.)

### Logic: one recap per round boundary

| Day-ending path | `day_turn` recap? | `day_close` recap? | Recaps |
|---|---|---|---|
| Round completes, Day continues (`new_rounds < 6`) | yes | not reached | **1** |
| Round-cap close (`new_rounds == 6`) | no (gated out) | yes | **1** |
| Mid-round execution (Day ends, game continues) | no (vote path, no wrap) | yes (names executed) | **1** |
| Vote-cap close (3rd failed vote) | no | yes | **1** |
| Immediate first-turn execution | no | yes | **1** |
| Game-ending execution / night kill | no | bypassed → `end_screen` | **0 (intended)** |

The `new_rounds < DAY_MAX_ROUNDS` gate is what prevents a double-post at the round-cap boundary: at the cap, `day_turn` stays silent and `day_close` owns that boundary's single recap.

---

## 3. Impact and Risk Analysis

**System Dependencies**

- **Dual-builder threading (`build_graph` + `build_runtime_graph`):** the flag must be wired through both, or local/remote diverge. The `graph_builder.py` docstring records a prior drift of exactly this shape (a missed career-emitter binding). **Mitigation:** explicit acceptance item + a test asserting both builders honor the flag.
- **`_render_context` / human UI:** unchanged — public `SystemMessage`s already flow to both AI context and UI. No prompt-template or UI change.

**Potential Risks & Mitigations**

- **Eval-metric contamination (assessed — not a risk).** The repeated near-identical recap could in principle inflate the repetition metric or the committed `evals/blunder-ledger.yaml`. Verified it does **not**: `eval_dialogue`, `repetition_experiment`, and `blunder_eval` all extract only `AIMessage`s with a player `name`; a Moderator `SystemMessage` is excluded by construction. **Mitigation:** add a regression test (`test_recap_excluded_from_ai_speech_extraction`) pinning that invariant so a future extraction refactor can't silently start scanning all lines.
- **Dual-mode byte-equal smoke (`tests/test_dual_mode_smoke.py`).** Stays green — recap is deterministic and identical across modes. **Mitigation/guard:** the renderer must use no RNG and no hash-order-dependent `set` iteration (dict/list iteration over `players` is insertion-ordered — automatic).
- **"Last message" endgame assertions (`tests/test_slice8_endgame.py`).** Safe **only if** the recap is never posted on the win path. **Mitigation:** recap lives in `day_close` (continues-to-Night only) and the round-wrap helper — never in/after `end_screen`; covered by the endgame tests staying green.
- **Replay safety.** `day_turn`'s `interrupt()` is the first side-effecting statement; on resume the node re-executes once and produces one recap append (no double-post). `day_close` has no interrupt. Confirmed safe.
- **Transcript preservation (spec 017, Eval Transcript Preservation).** Recaps will appear as `Moderator:` lines in preserved transcripts — desired (human evaluation), harmless to the writer.

---

## 4. Testing Strategy

All tests run in the fully-mocked suite (`uv run pytest -q`); `safe_llm` needs **no extension** (the recap introduces no LLM call site). Assertions are structural (counts, marker-substring presence, counter values) per the project's determinism posture (architecture §6, Determinism Posture & Testing Conventions) — never verbatim LLM text. New file: **`tests/test_slice_day_round_recap.py`**.

- **Pure renderer:** `render_day_round_recap` over hand-built state — no-execution vs with-execution (names player + side), singular/plural role counts, and `day_votes_initiated` = 0/1/3 for the votes line.
- **Node isolation:** `day_turn` appends exactly one recap on a continuing round-wrap and none mid-round; `day_close` posts the closing recap (execution case names the player + side; no-execution case); no double-post at the round-cap boundary.
- **End-to-end (compiled graph, `fake_large`/`fake_small`, pinned `_shuffle_order`, `GRAPHIA_ROLE=law-abiding`):** recap posted every round including the last (present even when counts are unchanged); **off-switch** (`GRAPHIA_DAY_ROUND_RECAP` off ⇒ zero recaps anywhere, all other Day behavior unchanged — also exercises the `partial` wiring); `day_votes_initiated` increments on human `/vote` and AI vote, resets in `day_open`.
- **Config unit:** `load_config()` reads `GRAPHIA_DAY_ROUND_RECAP` with default-on semantics.
- **Eval-isolation regression:** recap text absent from `blunder_eval._ai_lines_with_names` / `repetition_experiment._ai_speeches` / `eval_dialogue` extraction.
- **Existing tests:** none require changes (verified); the dual-mode smoke and endgame "last message" assertions stay green under the guards above.
