<!--
HOW to build the feature at an architectural level. Not a copy-paste guide.
-->

# Technical Specification: Long-Term Cross-Game Career Stats

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Add a new **`StatsStore`** abstraction that persists a single **rolling career aggregate** across game sessions, mirroring the existing dual-mode `DiaryStore` pattern (`src/graphia/diary_store.py`): a **local-file** implementation (JSON in the game's data dir) for local mode and an **AgentCore Memory** implementation (long-term scope) for remote mode, chosen by `make_stats_store(config)`. Unlike `DiaryStore`, whose local impl is ephemeral in-process, the local `StatsStore` is **file-backed** because career data must survive across runs.

**The UI/client layer owns all stats read+write — not the graph.** This is the load-bearing decision and it follows from the streaming model: the app already keeps a mode-agnostic state mirror, `GraphiaApp._latest_state` (`src/graphia/ui/app.py:146`), which is the only end-of-game state source that works in **both** local and remote mode (remote `graph.get_state` is empty). The graph streams with `stream_mode="updates"`, so `operator.add` reducer fields (e.g. `kill_log`) arrive as per-node **deltas** and `_latest_state` holds only the last delta, not the running total. We therefore add the per-game counters we need as **running-total, replace-semantics** `GameState` fields (each node returns the new full value), so they read correctly from `_latest_state` for both a normal end and an abandoned (Esc-quit) end.

Flow: at launch the UI reads the aggregate and renders the **greeting**; the game plays as today while the new counters accumulate in state; at game end the UI computes this game's **summary** from `_latest_state`, folds it into the aggregate via `StatsStore.record(...)`, and renders the **post-game panel**. An Esc-confirmed quit records an `"abandoned"` summary on the same path; `Ctrl+C` (`action_abort`, `app.py:332`) is left untouched and records nothing.

---

## 2. Proposed Solution & Implementation Plan

### 2.1 New module `src/graphia/stats_store.py`

Mirrors `diary_store.py`'s shape (Protocol + implementations + factory + pure data model).

**Data model (frozen dataclasses, primitive fields):**

| Type | Purpose | Key fields |
|---|---|---|
| `GameSummary` | one finished game's contribution (the delta) | `human_role` (`"mafia"`/`"law_abiding"`), `outcome` (`"law_abiding_win"`/`"mafia_win"`/`"draw"`/`"abandoned"`), `human_won: bool`, `rounds: int`, `votes_called: int`, `ballots_cast: int`, `night_attempts: int`, `night_successes: int`, `night_victims: int`, `day_executions: int` |
| `CareerStats` | the persisted rolling aggregate | `games_total`, `games_by_role`, `wins_by_role`, `abandoned_by_role`, `outcome_split` (`law_abiding_win`/`mafia_win`/`draw`/`abandoned`), lifetime `night_attempts`/`night_successes`/`votes_called`/`ballots_cast`, game-wide `total_day_executions`/`total_night_victims`, `completed_games`, `sum_rounds_completed` |

**Pure helpers (no I/O, unit-testable in isolation):**
- `fold(aggregate: CareerStats, summary: GameSummary) -> CareerStats` — folds one game into the aggregate. Win rate denominator = `games_by_role − abandoned_by_role`; `draw` counts as completed (in `completed_games`, `sum_rounds_completed`) but not a win. Abandoned games are excluded from `completed_games`/`sum_rounds_completed` (no meaningful length), included in `games_total`/`games_by_role`.
- `summarize(latest_state: dict, human_id: str, outcome: str) -> GameSummary` — extracts the per-game counters from `_latest_state`.
- `render_greeting(stats: CareerStats) -> str` and `render_panel(stats: CareerStats, last: GameSummary) -> str` — produce the display strings (greeting returns the first-run welcome line when `games_total == 0`; panel shows cumulative + per-game deltas). Win rate renders as not-applicable (`"—"`) when a role has zero completed games.

**`StatsStore` Protocol:**
- `load() -> CareerStats` — current aggregate; a zeroed `CareerStats` on first run / missing data.
- `record(summary: GameSummary) -> CareerStats` — read-modify-write: `fold` the summary in, persist, return the updated aggregate.

**Implementations:**
- `LocalFileStatsStore(path: Path)` — JSON object at `config.stats_file`. `load()` parses (zeroed `CareerStats` if missing/unparseable, logged, never raises). `record()` does an **atomic** write (temp file + `os.replace`) under a `threading.Lock`; parent dir `mkdir(parents=True, exist_ok=True)`.
- `AgentCoreMemoryStatsStore(memory_id, actor_id, session_id, region)` — rolling aggregate over AgentCore Memory's append-only event log: `load()` returns the newest `kind="career_aggregate"` event's body (or zeroed); `record()` reads newest, folds, and `create_event(...)` a new aggregate event. Uses the same boto3 surface as the diary store (`diary_store.py:160-186`) but at **long-term scope**: a **stable** `actor_id` (constant, e.g. `"human-career"` — NOT the per-game player id, which is random each game) and a constant `session_id` (e.g. `"career"`) so events accumulate across sessions under one logical record.
- `make_stats_store(config) -> StatsStore` — `memory_id` set (remote) → `AgentCoreMemoryStatsStore`; else `LocalFileStatsStore(config.stats_file)`. (Diverges from `make_diary_store` `diary_store.py:491`: no Gateway path; local impl is file-backed.)

### 2.2 Config — `src/graphia/config.py`

Add one field, derived like `log_file`/`checkpoint_dir`:

| Field | Source | Default |
|---|---|---|
| `stats_file: Path` | `GRAPHIA_STATS_FILE` | `./.graphia/career.json` |

`memory_id` (already present) drives remote selection; no other config change.

### 2.3 Game-state counters — `src/graphia/state.py` + nodes

Add to `GameState` (all `int`, **replace** semantics, init `0`): `human_votes_called`, `human_ballots_cast`, `human_night_attempts`, `human_night_successes`, `night_victim_count`, `execution_count`. Replace (not `operator.add`) is correct because each is written by a **single node per super-step** (no concurrent writers in the synchronous graph) and replace-with-running-total keeps `_latest_state` accurate under `stream_mode="updates"`.

Initialize the six fields to `0` in the setup node (where `players`/`human_id` are established). Increment points (each reads current value, returns current+1):

| Counter | Node | Condition |
|---|---|---|
| `human_votes_called` | `day_turn` / `_begin_vote` (`nodes/day.py:257,242`) | vote initiated and `active_vote.initiator == human_id` |
| `human_ballots_cast` | `collect_votes` (`nodes/day.py:453`), human branch (`:524`) | the human casts a ballot |
| `execution_count` | `resolve_vote` (`nodes/day.py:538`) | a vote resolves to an execution |
| `human_night_attempts` | `resolve_night_kill` (`nodes/night.py:184`) | human is alive Mafia and present in `night_picks` |
| `human_night_successes` | `resolve_night_kill` (`nodes/night.py:184`) | `night_picks[human_id] == victim.id` |
| `night_victim_count` | `resolve_night_kill` (`nodes/night.py:184`) | a victim died this Night |

`role` (`players[human_id].role`), `winner` (`state.py:58`), and `cycle` (`state.py:41`, used as `rounds`) are read directly by `summarize(...)`.

### 2.4 UI integration — `src/graphia/ui/app.py`

- **Store seam:** instantiate `make_stats_store(self.config)` at the top of `_drive` (`app.py:455`); also accept an injected store in `GraphiaApp.__init__` for tests (mirrors `build_graph(diary_store=...)`).
- **Greeting:** before gameplay output (top of `_drive`, before `build_graph`), `render_greeting(store.load())` → `#public-log` via `RichLog.write(Text(...))` (`app.py:174`). Written directly (not as a graph message), so it bypasses the `private_to` filter.
- **Post-game panel (normal end):** after `await drive_graph(...)` returns (`app.py:471`), derive `outcome` from `winner` vs the human's role, `summary = summarize(self._latest_state, self._human_id, outcome)`, `new = store.record(summary)`, then `render_panel(new, summary)` → `#public-log`, before the existing "Game over." banner.
- **Abandoned end (Esc-quit):** in `_on_quit_decision` (`app.py:401`), after the `if not confirm: return` guard (`app.py:429`) and **before** `call_after_refresh(self.exit)` (`app.py:434`) / `_arm_hard_exit_fallback()` (`app.py:440`): if a game is in progress (started, and not already `_game_over`/`phase=="end"`), record an `"abandoned"` summary. **Best-effort with timeout:** local file write is synchronous/instant; the remote Memory write runs with a short timeout and `try/except` — if it can't finish before the ~0.5s hard-exit fallback (`os._exit`, `app.py:397`), it is silently dropped (same net result as `Ctrl+C`). The write must run before the fallback is armed.
- **`Ctrl+C`:** `action_abort` (`app.py:332`) untouched.
- **Double-record guard:** pressing Esc on the end screen (already `_game_over`) records nothing.

### 2.5 Outcome / win mapping

`human_won = (winner == players[human_id].role)`. `outcome ∈ {law_abiding_win, mafia_win, draw, abandoned}`. `draw` (set at the cycle cap, `nodes/night.py:64`) is a completed game, not a win — included in win-rate denominator and average length, absent from `wins_by_role`. *(Note: the functional spec's outcome split listed three categories; `draw` is added here as a fourth, since the engine can produce it.)*

---

## 3. Impact and Risk Analysis

- **System Dependencies:** new module `stats_store.py`; edits to `config.py` (1 field), `state.py` (6 fields), setup/`day.py`/`night.py` nodes (init + 6 increments), `ui/app.py` (3 hook points + store seam). Reuses `config.memory_id`, `_latest_state`, the boto3 Memory surface already used by diaries. No new dependencies, no graph-topology change.
- **`stream_mode="updates"` correctness (core risk):** mitigated by using replace-semantics running counters. *If a future change introduces a second concurrent writer to any counter, it must move to a reducer* — noted so the invariant is explicit.
- **Remote abandon may not persist:** accepted — best-effort/timeout, dropped on slow network (graceful, matches `Ctrl+C`).
- **Local file integrity:** atomic temp-file+rename under a lock; unparseable/missing file → zeroed aggregate + log, never blocks startup. Missing keys in an older `career.json` default to `0` (forward-tolerant).
- **Memory read-modify-write race:** none in practice — one human, one in-progress game per process; documented assumption.
- **Greeting latency (remote):** `load()` adds one Memory read at startup; acceptable, and `try/except` → zeroed aggregate on failure so a Memory hiccup never blocks the game.
- **No console leakage:** all store failures log only (per architecture §5); the game panes stay clean.

---

## 4. Testing Strategy

- **Pure functions (no LLM, no UI, no AWS):** `fold` (role splits, win-rate denominator excludes abandoned, draw handling, average length), `summarize` (counter extraction from a crafted `_latest_state`), `render_greeting`/`render_panel` (first-run welcome, `"—"` win rate, delta lines).
- **`LocalFileStatsStore`:** round-trip on `tmp_path`; missing/corrupt file → zeroed; accumulation across successive `record` calls; atomic-write behaviour.
- **`AgentCoreMemoryStatsStore`:** mock at the boto3/Memory-client boundary (as the diary tests do); assert load-newest / fold / create-event and stable actor/session ids.
- **Node counters:** drive `resolve_night_kill`, `collect_votes`, `resolve_vote`, `day_turn` in isolation with crafted state (human as initiator / voter / mafia picker) and assert the returned counter values; reuse existing `target_human_pointing` / pointing fixtures.
- **UI (`App.run_test()`):** greeting renders the welcome line on first run and a summary with a seeded store; panel appears after a forced end; Esc-confirm triggers `store.record(outcome="abandoned")` while `Ctrl+C` does not; inject a fake in-memory `StatsStore` via the new seam.
- **Test isolation:** the new module must never reach real boto3 in the suite — tests inject the fake store or patch `make_stats_store`. (`stats_store` makes no LLM calls, so `safe_llm` needs no extension.)
