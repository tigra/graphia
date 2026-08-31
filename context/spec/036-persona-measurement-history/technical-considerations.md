<!--
Technical considerations for spec 036 — Persona-Generation Measurements Join the
Tracked Quality History. HOW an opt-in, clearly-labelled bench record is written
through the EXISTING renderer, and how the viewer learns to show its kind.
-->

# Technical Specification: Persona-Generation Measurements Join the Tracked Quality History

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

> **Deliberately additive to a committed data contract.** `evals/blunder-ledger.yaml` is repo-committed and append-only, and `evals/README.md` is its written contract. Every field this spec introduces is **conditional** — omitted when absent — so existing records stay byte-identical and remain readable. Nothing is backfilled.

> **Sibling in-flight specs.** **037** (transcript side panel) and **038** (colour-coded transcript view) both touch `src/graphia/ui/ledger_viewer.py`; this spec touches `src/graphia/eval_ledger.py` (the table/model layer) and `src/graphia/tools/`. The only shared surface is the viewer package — expect a merge on `ledger_viewer.py` only if 037 lands first, and none on the model layer.

---

## 1. High-Level Technical Approach

`persona_bench` currently prints a `BenchSummary` and writes nothing; a test pins that (`test_main_runs_end_to_end_and_writes_no_ledger`). This change adds an **opt-in** path that maps that same `BenchSummary` onto the **existing** `EvalResult` and writes it through the **existing** `render_record` / `append_record`, so one renderer keeps owning the fixed key order.

Three things make that mapping honest rather than a games-shaped struct with holes:

1. A new **`run.kind`** field names the record kind. Absent ⇒ a game run, so every existing record keeps its exact current meaning with no backfill. It is one of only two additions to the renderer — the game-only blocks (`outcomes`, `vote_activity`, `transcript_dir`) are **already** conditional and omit themselves.
2. The `quality` block keeps its `attempted` / `completed` / `duration_seconds` shape; **`run.kind` defines the unit** (games for a game run, rosters for a bench run).
3. Collision and regeneration counts describe the *generation process*, not run health, so they get their own small conditional block rather than being forced into `quality` or into `metrics`.

The four persona metrics are already in `METRIC_ORDER` (specs 032/033) and the **viewer** already reads them, so the viewer gains exactly one new column. **Correction found during implementation:** the **writer** never emitted them. `render_record` filtered metric sub-keys through a fixed tuple `("rate", "count", "denominator", "ci_low", "ci_high")` containing neither `mean` nor `peak`, so every value-type facet ever written reached the ledger as a bare `denominator:` with the measured value dropped. Specs 032/033 taught the reader and not the writer. Adding `mean`/`peak` to that tuple is therefore **in scope here** — it is additive (a metric carrying neither key renders byte-identically) and without it a bench record's entire payload would be four denominators. The already-written records cannot be backfilled: those values exist nowhere.

Affected files: `src/graphia/tools/persona_bench.py` (the opt-in flag + the mapping), `src/graphia/tools/blunder_eval.py` (`EvalResult` field, `render_record` conditional blocks), `src/graphia/eval_ledger.py` (the `Kind` column), `evals/README.md` (contract), `Makefile` (documented invocation), plus tests. **Unchanged:** `METRICS_VERSION`, every scorer, the game path, and every existing ledger record.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — the record kind (`blunder_eval.py`)

- **`EvalResult` gains `kind: str = ""`.** Empty ⇒ the renderer omits `run.kind` entirely, so a game run and every synthetic `EvalResult` in the existing tests render byte-identically to today. `run_eval` leaves it empty; only the bench path sets `"persona-bench"`.
- **`render_record` emits `run.kind` conditionally**, immediately after `run.date`, following the established pattern of `transcript_dir` / `settings.lineup` / `settings.scripted_player` — *"only emitted when the run recorded it"*. The fixed key order is otherwise untouched.
- **No `METRICS_VERSION` bump.** The bump rule is documented in the module: a version bump means a **scoring rule** changed. This adds a record kind and reuses existing scorers unchanged, so rates measured before and after stay comparable — the same reasoning already applied to `ci_low`, `lineup` and `scripted_player`.

| field | block | type | conditional? | meaning |
| --- | --- | --- | --- | --- |
| `kind` | `run` | str | yes — omitted when empty | `persona-bench`; absent ⇒ game run |
| `rosters` *(see B)* | `quality` | int | — | reuses `attempted`/`completed` keys |
| `collisions` | `generation` | int | yes — whole block omitted for game runs | casts that ended with an over-similar pair |
| `regenerations` | `generation` | int | yes | regeneration attempts that fired |

### Component B — the bench → record mapping (`persona_bench.py`)

- **A `--record` flag, default off.** Off ⇒ today's behaviour exactly, which keeps `test_main_runs_end_to_end_and_writes_no_ledger` valid as a live guard rather than a test to delete. On ⇒ build an `EvalResult` and append.
- **The mapping**, all from the existing `BenchSummary`:
  - `kind` = `"persona-bench"`; `provider` / `large_model` / `small_model` from the resolved config (reuse `_resolved_model_names`).
  - `quality`: `attempted` = `rosters_attempted`, `completed` = `rosters_completed`, `duration_seconds` = `duration_seconds`.
  - `metrics`: the four persona facets in their **existing value-type shape** (`{mean|peak, denominator}`), omitting the semantic pair entirely when `--semantic` was not passed or embeddings were unavailable — the absent-not-zero rule the scorers already follow.
  - `generation`: `collisions` = `total_collisions`, `regenerations` = `total_regenerations`.
  - `settings`: the resolved persona knobs — diversity flag, collision threshold, regeneration attempts, temperature — so an A/B pair is readable as a pair (functional-spec §2, "the conditions the measurement ran under").
  - `code` / `provider_block`: reuse the **same provenance collector the eval uses**, so a bench record carries the same commit/branch/dirty and model digests. Do not hand-roll.
  - `notes`: from an existing `--note` argument (add if absent), rendered last by the existing renderer.
- **`outcomes` and `vote_activity` need no work at all.** Both are **already conditional** in `render_record` (`if result.outcomes:` / `if result.vote_activity:`), so leaving them empty on a bench record omits them automatically. Verified against the current source — **no renderer change is required for this**, and the bench path must simply not populate them.

### Component C — the `Kind` column (`eval_ledger.py`)

- Add one short entry to `_FIXED_COLUMNS`. Placement: with the identity columns (`Date` / `Provider`), **not** in the metric tail — the UI right-justifies the last `len(METRIC_ORDER)` columns, so anything added to the head stays left-justified automatically (the spec-029 precedent, documented in that tuple's comment).
- Read it in `_row_cells` via the existing `_dig(record, "run.kind", default=…)`, rendering game runs as a stable label (blank or `game`) and bench runs as `persona`. No new render branch — `_dig`'s defensive default is what makes pre-036 records render.
- Add `run.kind` to the searchable fields so a reviewer can filter to one kind.
- **`render_detail` DOES need work — corrected during implementation.** It does **not** iterate the blocks present: `_render_run_section` renders a fixed list (`date`, `duration_seconds`, `metrics_version`, plus `games` when present), and there is no section renderer for a `generation` block. Verified empirically — no rendered detail contains `kind:`. So the drill-down needs (a) a conditional `kind` line in `_render_run_section`, mirroring the existing conditional `games` line, and (b) a `_render_generation_section` plus its entry in `render_detail`'s `sections` list. This does not block Slice 1: the table column alone satisfies the functional spec's "identifiable in the viewer" criterion.

### Component D — contract + docs

- `evals/README.md`: document `run.kind` (absent ⇒ game run), the `generation` block, the unit-follows-kind rule for `quality`, and restate that **records compare only within a kind as well as within a provider**.
- `Makefile`: document the recording invocation on the `persona-bench` target.

### What does NOT change

`METRICS_VERSION`; any scorer or metric definition; the game path; `run_eval`; existing ledger records; the transcript machinery (a bench run plays no game and writes no transcript, so `transcript_dir` stays absent).

---

## 3. Impact and Risk Analysis

- **System dependencies.** `blunder_eval` (record type, renderer, provenance collector), `persona_bench` (the source measurement), `eval_ledger` (table model), and the committed ledger plus its README contract.

- **Risk: silently corrupting a committed data file.** *Mitigation:* every new field is conditional and additive; the acceptance bar is that existing records are **byte-identical** after the change. A test should render a known synthetic `EvalResult` and assert the output matches today's expected text exactly.

- **Renderer edits are confined to one additive line.** The only change to `render_record` is the conditional `run.kind` emission (plus the conditional `generation` block); `outcomes` and `vote_activity` were already conditional, so nothing about game-run rendering is touched. *Still pin it:* a test asserting a game-shaped `EvalResult` renders both blocks and a bench-shaped one renders neither, so a future refactor cannot quietly make them unconditional.

- **Risk: a bench record read as a game run.** A 0-of-0 win rate or an empty vote block could be mistaken for a failed game. *Mitigation:* the `Kind` column (Component C), plus omitting rather than zeroing those blocks. This is the same hazard as the backlog's *"Display partially-complete eval runs"* item — the labelling here should be what that item builds on, not a competing answer.

- **Risk: tests writing to the real ledger.** The bench previously could not touch it at all; now it can. *Mitigation:* every test of the recording path must redirect `blunder_eval.LEDGER_PATH` at `tmp_path`. This project has already had ~25 synthetic records leak into the committed ledger from exactly this class of bug, so treat the redirect as mandatory, and keep the existing flag-off no-write test as a live guard.

- **Cost / determinism.** Recording is metadata only — no extra model calls, no change to what a bench run costs. `--semantic` remains the only paid part and is unchanged.

- **Opt-in is a deliberate ergonomics/noise trade,** not a technical limit: the bench's value is dev-loop speed (~30 s per roster), so most runs are throwaway and auto-recording would bury the rare real measurement.

---

## 4. Testing Strategy

All offline and mocked — the bench's model calls go through the `safe_llm` fakes, so no test reaches a real model or real embeddings.

- **Renderer, additive-only:** a synthetic game-shaped `EvalResult` renders **byte-identically** to the current expected text (no `run.kind`, no `generation`, `outcomes`/`vote_activity` present); a bench-shaped one renders `run.kind: 'persona-bench'`, the `generation` block, and **omits** `outcomes` / `vote_activity` / `transcript_dir`; key order is asserted for both.
- **Flag gating:** `--record` absent ⇒ the ledger is untouched (extend the existing no-write test); `--record` present ⇒ exactly one document is appended and every prior document is unchanged.
- **The mapping:** roster counts land in `quality`; collisions/regenerations in `generation`; the persona knobs in `settings`; the four metric facets keep their value-type shape; the semantic pair is **absent** when not measured.
- **Provenance:** a recorded bench run carries the same `code` and `provider` block shape a game run does.
- **Viewer:** `build_table_model` renders the `Kind` cell for a bench record, a stable label for a record without the field, and does not raise on either; `render_detail` shows the `generation` block; the metric columns need no new assertions beyond the existing value-type ones.
- **Ledger isolation:** every recording test redirects `LEDGER_PATH` to `tmp_path`; the committed ledger is asserted untouched.
- Full `uv run pytest -q` green.

**Out of suite (developer-run, optional):** one real `make persona-bench ARGS="--provider ollama --rosters 5 --diversity on --record"` to confirm the record appears in `make view-ledger` with its `Kind` cell and metrics — ollama is free, so this costs nothing but time.
