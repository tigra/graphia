# Tasks: Long-Term Cross-Game Memory & Career Stats

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Technical Considerations:** [`./technical-considerations.md`](./technical-considerations.md)
- **Architecture decision:** ADR 007 (*Two-Tier Long-Term Memory — Exact-Counter Records Now, Semantic Memory Later*) — these tasks cover **Layer 1 only** (exact custom long-term-Memory records); Layer 2 (semantic/summarization narrative memory) is deferred to a later phase.

**Verification posture:** all slices are verified with `uv run pytest` — unit tests for pure functions/stores plus Textual `App.run_test()` for UI. This is a TUI + all-mocked pytest project, so there is no browser MCP; real boto3/Bedrock must never be reached from the suite. Terraform is checked with `terraform validate` + `plan` via the `./tf` container wrapper — never `apply` (live apply is run by the user). Slices 1–5 are local-mode and keep the app runnable with no AWS; slices 6–7 add remote mode while leaving local mode intact.

---

- [x] **Slice 1: Pre-game greeting (first-run welcome) + local store seam**
  - [x] Add `src/graphia/stats_store.py` scaffold: frozen `CareerStats` (zeroed default) + `GameSummary` dataclasses, `StatsStore` Protocol (`load`/`record`), and `render_greeting` (first-run welcome line when `games_total == 0`). **[Agent: python-backend]**
  - [x] Implement `LocalFileStatsStore.load()` (zeroed `CareerStats` on missing/unparseable file, logged, never raises) + `make_stats_store(config)` returning the local store. **[Agent: python-backend]**
  - [x] Add `config.stats_file` (`GRAPHIA_STATS_FILE`, default `./.graphia/career.json`), derived like `log_file`/`checkpoint_dir`. **[Agent: python-backend]**
  - [x] Wire the store seam in `ui/app.py`: build via `make_stats_store(self.config)` in `_drive`, injectable through `GraphiaApp.__init__` for tests; render `render_greeting(store.load())` to `#public-log` before gameplay (bypasses the `private_to` filter). **[Agent: textual-tui]**
  - [x] Tests: `render_greeting` first-run welcome; `LocalFileStatsStore.load()` missing→zeroed; `App.run_test()` greeting shows the welcome line on first launch. Run `uv run pytest`. **[Agent: testing]**

- [x] **Slice 2: Record win/loss by role; greeting + post-game panel**
  - [x] `fold(aggregate, summary)` for `games_total`, `games_by_role`, `wins_by_role`, `completed_games`, `sum_rounds_completed`, `outcome_split` (law_abiding_win / mafia_win / draw); `summarize(latest_state, human_id, outcome)` extracting role / outcome / `human_won` / rounds. **[Agent: python-backend]**
  - [x] `LocalFileStatsStore.record()` — read-modify-write: `fold` then atomic temp-file + `os.replace` under `threading.Lock`, parent `mkdir(parents=True, exist_ok=True)`; full `render_greeting` (games + win rate by role, `"—"` when a role has no completed games) and `render_panel` (cumulative + per-game deltas). **[Agent: python-backend]**
  - [x] `ui/app.py`: outcome mapping (`human_won = winner == players[human_id].role`; `outcome ∈ {law_abiding_win, mafia_win, draw}`); after `drive_graph(...)` returns → `summarize`→`record`→`render_panel` to `#public-log` before the existing "Game over." banner. **[Agent: textual-tui]**
  - [x] Tests: `fold` role splits + win-rate denominator + `draw`-is-not-a-win; `summarize` from a crafted `_latest_state`; local store round-trip / accumulation / atomic-write; `App.run_test()` panel after a forced end + non-empty greeting on the next launch. Run `uv run pytest`. **[Agent: testing]**

- [ ] **Slice 3: Day-action counters (votes called, ballots cast)**
  - [x] `state.py`: add `human_votes_called`, `human_ballots_cast` (`int`, replace semantics, initialized to `0` in the setup node). **[Agent: langgraph-agentic]**
  - [x] `nodes/day.py`: increment `human_votes_called` in `_begin_vote` when `active_vote.initiator == human_id`; `human_ballots_cast` in the `collect_votes` human branch (each reads current value, returns current+1). **[Agent: langgraph-agentic]**
  - [x] Extend `GameSummary` / `CareerStats` + `fold` + `summarize` to carry `votes_called` / `ballots_cast`; add the greeting/panel lines. **[Agent: python-backend]**
  - [ ] Tests: node counter tests (human as vote initiator / ballot caster) with crafted state + existing pointing fixtures; extended `fold` / `summarize`; panel delta line. Run `uv run pytest`. **[Agent: testing]**

- [ ] **Slice 4: Night-kill counters + game-wide totals**
  - [ ] `state.py`: add `human_night_attempts`, `human_night_successes`, `night_victim_count`, `execution_count` (init `0` in setup). **[Agent: langgraph-agentic]**
  - [ ] `nodes/night.py` `resolve_night_kill`: `night_victim_count` (a victim died), `human_night_attempts` (human is alive Mafia and present in `night_picks`), `human_night_successes` (`night_picks[human_id] == victim.id`); `nodes/day.py` `resolve_vote`: `execution_count` when a vote resolves to an execution. **[Agent: langgraph-agentic]**
  - [ ] Extend `fold` / `summarize` for lifetime `night_attempts` / `night_successes`, game-wide `total_day_executions` / `total_night_victims`, and average game length (`sum_rounds_completed` / `completed_games`); render kills attempted-vs-successful + game-wide totals clearly distinct from the player's personal numbers. **[Agent: python-backend]**
  - [ ] Tests: `resolve_night_kill` (human backs the killed target / backs a non-killed target / victim died) + `resolve_vote` execution; `fold` game-wide totals + average length; render. Run `uv run pytest`. **[Agent: testing]**

- [ ] **Slice 5: Abandoned-game recording (Esc-confirmed quit)**
  - [ ] `fold` handles `"abandoned"`: `+1` to `games_total` / `games_by_role` / `abandoned_by_role` / `outcome_split.abandoned`; excluded from `completed_games` / `sum_rounds_completed` / the win-rate denominator. **[Agent: python-backend]**
  - [ ] `ui/app.py` `_on_quit_decision`: after the confirm guard and **before** `call_after_refresh(self.exit)` / `_arm_hard_exit_fallback()`, if a game is in progress (started, not already `_game_over` / `phase == "end"`) record an `"abandoned"` summary — best-effort with a short timeout (local write is instant; remote write dropped if it can't beat the hard-exit fallback); double-record guard on the end screen; `Ctrl+C` (`action_abort`) untouched. **[Agent: textual-tui]**
  - [ ] Tests: `fold` abandoned (counted in games / outcome-split, excluded from win-rate / avg-length); `App.run_test()` Esc-confirm records `outcome="abandoned"`, `Ctrl+C` records nothing, Esc on the end screen does not double-record. Run `uv run pytest`. **[Agent: testing]**

- [ ] **Slice 6: AgentCore long-term-record remote backend + equivalence**
  - [ ] `config.py`: add `stats_strategy_id` (`GRAPHIA_STATS_STRATEGY_ID`) and `stats_namespace` (`GRAPHIA_STATS_NAMESPACE`, default `/career/human-career/`), remote-only, plumbed from `terraform output` like `memory_id` / `gateway_url`. **[Agent: python-backend]**
  - [ ] `AgentCoreLongTermStatsStore`: `load()` via `ListMemoryRecords` filtered to the career namespace (zeroed `CareerStats` if absent); `record()` folds the summary then writes via `BatchUpdateMemoryRecords` (or `BatchCreateMemoryRecords` on first write) with `content` (the `CareerStats` JSON), `namespaces=[namespace]`, the self-managed `memoryStrategyId`; stable `actor_id="human-career"`; **verify the vendored `bedrock_agentcore` SDK's batch-record surface and fall back to direct boto3 `bedrock-agentcore` data-plane calls if unwrapped**; `make_stats_store` selects the remote store when `memory_id` is set. **[Agent: langgraph-agentic]**
  - [ ] Tests: mock at the boto3 `bedrock-agentcore` data-plane boundary (assert `load()` lists records by namespace; `record()` folds then `batch_update`/`batch_create` with the exact `content` / `namespaces` / `memoryStrategyId` and stable `actor_id`); **equivalence test** that local-file and long-term-record impls produce identical `CareerStats` for the same game sequence; ensure no real boto3 is reached in the suite. Run `uv run pytest`. **[Agent: testing]**

- [ ] **Slice 7: Terraform — self-managed memory strategy + required scaffolding**
  - [ ] Add a self-managed (custom) memory strategy to the existing AgentCore Memory resource, plus the S3 bucket + SNS topic + IAM role its payload-delivery config requires (per ADR 007 — required even though the auto-extraction trigger is never fired); expose `stats_strategy_id` + `stats_namespace` as `terraform output`s and plumb them into the runtime environment like `memory_id` / `gateway_url`. **[Agent: terraform-aws]**
  - [ ] Validate with `terraform validate` + `plan` via the `./tf` container wrapper (no `apply` — live apply is run by the user); confirm the new outputs appear in the plan. **[Agent: terraform-aws]**
