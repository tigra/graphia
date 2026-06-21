# Technical Specification: Scripted-Player's-Side Win Rate in Evals

- **Functional Specification:** [`context/spec/027-scripted-side-win-rate/functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

The eval harness already records win-rate **by side** in each ledger record's `outcomes` block (`src/graphia/tools/blunder_eval.py`, `tally_outcomes`): `law_abiding` / `mafia` each as `{wins, rate, ci_low, ci_high}` over the run's completed games. This change adds **one more entry** to that same `outcomes` block — the **scripted-side win rate**: the fraction of the run's games in which *the side the scripted stand-in was on* won, with the same Wilson 95% band and a label of which side that was.

The work is **pure measurement, harness-only**. It is additive to the `outcomes` block, computed per game from the scripted seat's dealt side (so it stays correct even if the side were to vary), surfaced in the run summary, rendered defensively in `render_record`, read defensively by the spec-012 ledger viewer, and **does not bump `metrics_version`** (the `ci_low` / `outcomes` / `lineup` / spec-026 `settings.scripted_player` precedent). No gameplay, node, schema, graph, or prompt code is touched.

**Systems affected** (all within `src/graphia/tools/blunder_eval.py`, plus the doc legend):
- `_GameCapture` / `_play_one_game` — thread the scripted seat's per-game side alongside the existing `winner`.
- `tally_outcomes` — accept the per-game scripted-seat sides and add the `scripted_side` entry to the block.
- `run_eval` — fold the per-game sides; surface the new rate in the console summary.
- `render_record` — emit the new `outcomes` entry, conditionally (back-compat).
- `src/graphia/eval_ledger.py` — the viewer already reads `outcomes` defensively; one optional detail-render line.
- `evals/README.md` — one new line in the `outcomes` legend.

This spec lands **with or after spec 026** (Active Scripted Player), which shares the same `blunder_eval` surface: 026 makes the seat's side per-run selectable and records `settings.scripted_player`; 027 reads the **per-game scripted seat side** to compute the rate. 027 is meaningful for the passive stand-in too (it reports the passive side's near-zero rate).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Where the per-game scripted-seat side is read (VERIFIED)

The scripted seat is the human seat. Its dealt side per game is `players[human_id].role`, where:

- `GameState` carries `human_id: str` and the player map `players: dict[str, PlayerState]`, and `PlayerState.role` is `Literal["mafia", "law_abiding"]` with `is_human: bool` (`src/graphia/state.py`). **The `role` token spelling is the underscore form `"law_abiding"` / `"mafia"` — identical to the `winner` values `tally_outcomes` already matches**, so no remapping is needed: the side string drops straight into the existing `_OUTCOME_SIDES` vocabulary.
- The run-level pin comes from `GRAPHIA_ROLE` → `config.human_role` (`"law_abiding"` / `"mafia"` / `None`); `blunder_eval.main()` `setdefault`s `GRAPHIA_ROLE=law-abiding` today, and spec 026 makes it per-run selectable. The dealt seat role is the authoritative per-game value (config is the *requested* role; the deal is what actually happened).
- In `_play_one_game`, roles are already read once after the deal — `players_now = graph.get_state(run_config).values.get("players", {})` — and the final state is read again at game end. `_GameCapture` already snapshots `players=state.get("players", {})` and `winner=state.get("winner")`. **It does NOT currently carry `human_id`.** That is the one missing wiring: capture `human_id` (`state.get("human_id")`) so the side can be resolved per game from `cap.players[cap.human_id].role`.

**Verified finding:** the per-game scripted side derives from the *dealt seat role* in the same final state `_GameCapture` already reads — `players[human_id].role` — using the underscore token that already matches `winner`. The scripted side won a game iff `winner == that game's seat role`. A `no_winner` / `runaway` game has `winner` ∉ `{"law_abiding", "mafia"}`, so it is automatically a non-win under that equality. The only code change to enable it is threading `human_id` into `_GameCapture` (or, equivalently, resolving and storing the side string directly per game in `run_eval`). I confirmed this from `state.py`, `config.py`, and the `_play_one_game` / `run_eval` / `_GameCapture` read sites; I did not run a game.

### 2.2 Per-game side collection: `_GameCapture` + `_play_one_game`

- **`_GameCapture`** gains one field — either `human_id: str` (mirroring the already-snapshotted `players` map) **or** a resolved `scripted_side: str | None` (the dealt seat role, or `None` if unresolvable). Preferred: capture `human_id` (minimal, raw — keeps the side resolution in `run_eval` beside the other per-game folds, matching how `players`/`winner` are stored raw and scored in `run_eval`). Default it (`field(default="")` / `None`) so a hand-built `_GameCapture` in an offline test needs no extra wiring.
- **`_play_one_game`** sets it from the same final-state read it already does: `human_id=state.get("human_id", "")`.

### 2.3 The tally: `tally_outcomes`

`tally_outcomes(winners)` becomes `tally_outcomes(winners, scripted_sides)` (or accepts a parallel list of per-game scripted seat sides). Responsibilities:

- Keep the existing four/five buckets (`law_abiding` / `mafia` side rates, `runaway` / `draw` / `no_winner` bare counts) exactly as today.
- Add a new **`scripted_side`** entry mirroring the per-side shape, computed **per game**:
  - **Numerator** — count of games where `winner == that game's scripted seat side`.
  - **Denominator** — **all** completed games (`games`, the same denominator the side rates use). A `no_winner` / `runaway` game contributes to the denominator but never to the numerator (its `winner` is not a side, so the per-game equality is false).
  - **Shape** — `{side, wins, rate, ci_low, ci_high}` where `side` is the scripted seat's side label (e.g. `"law_abiding"`). Reuse `wilson_ci(wins, games)` for the band — the exact helper the side rates use. (Per game `_attach_ci` is for the `metrics` map; `tally_outcomes` calls `wilson_ci` directly, as it already does for the side rates.)
  - **`games == 0`** — emit `{side, wins}` with rate/CI omitted (mirroring the side-rate `games == 0` path; no `ZeroDivisionError`).
  - **`side` when it varies / is unknown** — in the spec-026 per-run-pinned default the side is constant, so `scripted_side.side` is that single label and `scripted_side.rate` equals the matching by-side rate. The per-game computation is what makes it *correct* even if a future run varied the seat side game-to-game (the rate is still scripted-wins ÷ all games; the `side` label is then the constant pin, or — if genuinely mixed — left to a tunable, e.g. the most-common seat side, with the rate still the per-game count). Default: a single constant pin per run, so `side` is unambiguous. If no game resolved a side (all `human_id` missing), omit the entry entirely (treat like an absent metric — defensive, never a misleading `0`).
- **Render order**: insert `scripted_side` into the fixed key order **after `mafia`** and **before `runaway`** (it is a side-shaped rate, so it belongs with the side rates, before the bare counts): `games → law_abiding → mafia → scripted_side → runaway → draw → no_winner → note`.
- **Invariant unchanged**: `scripted_side` is a *derived view* of the same games (it equals one of the side rates in the pinned case), not a new partition bucket, so the existing partition invariant `law_abiding.wins + mafia.wins + runaway + draw + no_winner == games` is untouched.

`tally_outcomes` stays **pure** (plain lists in, render-ready mapping out) — precisely unit-testable.

### 2.4 Folding in `run_eval`

`run_eval` already accumulates `winners: list[str | None]` across completed games and calls `result.outcomes = tally_outcomes(winners)`. Add a parallel `scripted_sides: list[str | None]` accumulated in the same per-game loop (`scripted_sides.append(_seat_side(cap))`, where `_seat_side` resolves `cap.players.get(cap.human_id)` → `.role`, defensive on a missing/None seat), and pass it to `tally_outcomes`. Failed-early games never produce a `_GameCapture`, so they are excluded exactly as `winners` already is.

### 2.5 Run summary line

The console summary in `main()` (after `run_eval`) gains one line, read from `result.outcomes["scripted_side"]` when present:

```
scripted side (law_abiding): won 11/20 (rate=0.55, 95% CI [0.34–0.74])
```

Read defensively (`result.outcomes.get("scripted_side")`), so a run that produced no resolved side simply omits the line. The existing per-side summary is unaffected (today the summary prints only `repetition`; this adds the scripted-side line as the headline KPI for the 026 experiment, alongside the games/lines counts).

### 2.6 Rendering: `render_record`

In the existing `outcomes:` block emission (`render_record`), after the `law_abiding` / `mafia` side loop and **before** the `runaway`/`draw`/`no_winner` bare-count block, emit `scripted_side` with the **same conditional, same fixed sub-key order** the side rates use:

- Only when `result.outcomes.get("scripted_side")` is a dict (a synthetic/older `EvalResult` without it omits the key entirely — back-compat).
- Sub-key order `side → wins → rate → ci_low → ci_high`, each emitted only `if key in facets` (so the `games == 0` path drops rate/CI, exactly like the side rates).
- Uses the existing `_yaml_block` helper; `side` renders as a quoted scalar via the existing scalar path.

Example emitted block fragment:

```yaml
outcomes:
  games: 20
  law_abiding: {wins: 11, rate: 0.55, ci_low: 0.342, ci_high: 0.742}
  mafia: {wins: 6, rate: 0.3, ci_low: 0.145, ci_high: 0.519}
  scripted_side:                 # NEW (spec 027) — the seat's own side rate
    side: 'law_abiding'
    wins: 11
    rate: 0.55
    ci_low: 0.342
    ci_high: 0.742
  runaway: 0
  draw: 0
  no_winner: 3
  note: '…passive scripted human caveat…'
```

(In the pinned-LA run above, `scripted_side.rate == law_abiding.rate` by construction; a Mafia-pinned run would have `side: 'mafia'` and `scripted_side.rate == mafia.rate`.)

### 2.7 Viewer: `src/graphia/eval_ledger.py`

The pure ledger layer reads `outcomes` defensively via `_dig` throughout, so an absent `scripted_side` on any pre-027 record already resolves cleanly to nothing. Minimal additive change:

- **Detail view** (`_render_outcomes_section`): add a `scripted_side` sub-block after the `law_abiding` / `mafia` sides and before `runaway`, rendered only when the key is present (reuse the existing `_field` / `_format_outcome_rate`-style defensive helpers; the `side` label plus the `wins` / full-precision `rate` / `[ci_low–ci_high]` band). When absent, render nothing extra — a pre-027 record's `outcomes` section is unchanged.
- **Table cell**: no new column is required for v1 (the existing `Wins (LA/M)` column already shows both side rates; in the pinned case the scripted-side rate equals one of them). Optional, deferred: surface it in the table later if desired — out of scope here to keep the column count stable.
- No `metrics_version` read/branch changes; no search-field changes required.

### 2.8 Documentation: `evals/README.md`

Add one entry to the `outcomes` field legend and the record-shape example: `scripted_side` — `{side, wins, rate, ci_low, ci_high}`, the scripted stand-in's-side win rate (scripted-side wins ÷ all games, `no_winner`/`runaway` = non-win), with its Wilson 95% band and the side label; **additive, not retro-filled** — pre-027 records simply omit it, read as absent. Note it equals the matching by-side rate when the seat side is pinned per run (the spec-026 default).

---

## 3. Impact and Risk Analysis

- **System dependencies.** Shared `blunder_eval` `outcomes` surface with spec 013 (the block), spec 023 (`runaway`), and spec 026 (`settings.scripted_player`, per-run seat side). 027 reads the per-game seat side that 026 makes selectable; it **lands with or after 026** so the side is a deliberate, recorded per-run value. State fields read: `human_id`, `players[*].role`, `winner` — all already public in `GameState` and already snapshotted by `_GameCapture` (only `human_id` is newly threaded).

- **Per-game-side derivation correctness (the load-bearing bit).** The rate hinges on resolving each game's scripted seat side from `players[human_id].role` and comparing it to `winner`. Verified that the `role` token (`"law_abiding"`/`"mafia"`) matches the `winner` vocabulary exactly, so the per-game equality `winner == seat_side` is a direct string compare with no remapping. Risk: a missing/unresolved `human_id` (a malformed final state) → that game's side is `None`; mitigation: such a game is excluded from the numerator and, if *no* game resolves, the entry is omitted entirely (absent, never a misleading `0`). Asserted by the LA-seat / Mafia-seat / mixed tests (§4).

- **Back-compat with committed passive baselines.** Every committed baseline predates 027 and lacks `scripted_side`. The viewer's `_dig`-based reads and `render_record`'s `if key in facets` guards mean those records render with the new field simply absent — no error, no retro-fill. The functional spec forbids re-scoring recorded games. Asserted by a render-without-the-field test and a viewer-reads-pre-027-record test (§4).

- **No gameplay change (pure measurement).** No node, schema, graph, or prompt is touched; the same games produce the same outcomes, only the recorded `outcomes` block gains one entry. This metric is **not** effort-not-results — it is a deterministic tally that *enables* the spec-026 effort-not-results read (the one comparable number across an LA batch and a Mafia batch).

- **No `metrics_version` bump.** `scripted_side` is an outcome/provenance addition over the same recorded winners — the `ci_low` / `outcomes` / `lineup` / `settings.scripted_player` precedent. Bumping would falsely flag every prior blunder rate as incomparable.

- **Determinism.** The tally is a pure function of the already-recorded per-game `(winner, seat_side)` pairs — no model, no RNG — so it is precisely unit-testable and stable across reruns of the same recorded data.

---

## 4. Testing Strategy

Pure-tally unit tests, no live model and no RNG — the same posture as the existing `tally_outcomes` / `score_*` tests in the suite. New cases in the slice-numbered file for this spec (e.g. `tests/test_slice27_scripted_side.py`); each builds synthetic per-game `(winner, seat_side)` inputs (and, for the render/viewer tests, a synthetic `EvalResult.outcomes` / `RawRecord`).

Test intents (mapping the functional-spec acceptance criteria):

- **LA-seat run equals `law_abiding.rate` (AC1, AC2).** A run of games all dealt an LA scripted seat → `outcomes["scripted_side"].rate == outcomes["law_abiding"].rate`, `side == "law_abiding"`, and `wins` equals the LA win count.
- **Mafia-seat run equals `mafia.rate` (AC2).** Same games dealt a Mafia scripted seat → `scripted_side.rate == mafia.rate`, `side == "mafia"`.
- **`no_winner` / `runaway` is a non-win but counts toward total (AC2 of FR §2 "all games").** A game whose `winner` is `None` or `"runaway"` contributes to `games` (the denominator) but never to `scripted_side.wins`; assert `wins / games` reflects the larger denominator.
- **Per-game correctness under a varying seat side.** A constructed run where the seat side differs across games → `scripted_side.wins` counts only games where `winner == that game's seat side` (proves the per-game derivation, not a single by-side read).
- **Wilson CI attached.** `scripted_side` carries `ci_low`/`ci_high` from `wilson_ci(wins, games)`; assert they match the side-rate band in the pinned case (same `wins`/`games`).
- **`games == 0` path.** Empty run → `scripted_side` is `{side, wins: 0}` with rate/CI omitted (no `ZeroDivisionError`), mirroring the side-rate path.
- **No resolved side → entry omitted (defensive).** All games with an unresolvable seat side → `scripted_side` is absent from the block (treated like an absent metric).
- **`render_record` emits the entry.** An `EvalResult` whose `outcomes["scripted_side"]` is set → the rendered document contains the `scripted_side:` block in the fixed order, after `mafia`, before `runaway`.
- **`render_record` back-compat.** An `EvalResult.outcomes` without `scripted_side` (a synthetic/older shape) renders the `outcomes` block with no `scripted_side` key and without error.
- **Viewer reads a pre-027 record cleanly.** A `RawRecord` whose `outcomes` lacks `scripted_side` → `render_detail` produces no `scripted_side` line and does not raise (the `_dig` defensive path); a record *with* it renders the new detail line.
- **Run summary line.** The summary helper, given a `result.outcomes` with `scripted_side`, prints the scripted-side line (rate + side + CI); given one without it, prints nothing extra.

**Out-of-suite (no pytest):** there is no real-model assertion here — `scripted_side` is a deterministic tally over recorded outcomes. Its *use* is the spec-026 effort-not-results comparison (`make blunder-eval` active vs the passive baseline, read the new scripted-side rate per LA / Mafia batch), which lives in that spec's out-of-suite plan, not here.

---

## Resolved decisions

1. **`_GameCapture` field shape → carry raw `human_id`.** `_GameCapture` gains `human_id` (raw), and `run_eval` resolves the per-game side via `cap.players.get(cap.human_id).role` beside the other per-game folds — matching how `players`/`winner` are stored raw and scored in `run_eval`. (Both shapes pass the same tests; this keeps the capture minimal.)
2. **`scripted_side.side` label → the run's constant pinned side.** The spec-026 default pins one seat side per run, so `side` is that single unambiguous label and the per-game `rate` equals the matching by-side rate. The rate is computed per game so it stays correct even if a future run mixed seat sides; in that (non-default) case the single `side` label defaults to the constant pin — a representative label for genuinely-mixed runs is a future tunable, not exercised by the default.
