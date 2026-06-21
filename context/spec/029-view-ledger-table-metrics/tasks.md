# Tasks: Show Newer Eval Metrics in the view-ledger List (Spec 029)

Display-only — adds curated columns to the `make view-ledger` list. No gameplay change, no new metric, **no `metrics_version` bump** (ADR-011 exempt). Independent of specs 028/030.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: The view-ledger list shows the newer tracked metrics as curated columns**
  - [x] Add three cell extractors in `src/graphia/eval_ledger.py`, mirroring `_outcomes_cell` / `_vote_activity_cell` (defensive `_dig` / `_MISSING`): `_scripted_side_cell` → **`Scripted (side)`** (`outcomes.scripted_side`; e.g. `LA .55`; blank when absent), `_stand_in_cell` → **`Stand-in`** (`settings.scripted_player`; **defaults to `passive`** on pre-026 records per the README contract, not blank), `_resolution_cell` → **`Unres (R/N)`** (`outcomes.runaway` + `outcomes.no_winner`; present-zero `R 0 N 0` distinct from the absent-block blank). Declare the three headers in `_FIXED_COLUMNS` **before `Notes`** with matching positional calls in `_row_cells`. The viewer (`ui/ledger_viewer.py`) adds columns positionally and never names them, so **no viewer edit** is needed; the detail view (`render_detail`) is unchanged (already complete). **[Agent: langgraph-agentic]**
  - [x] Tests in `tests/test_ledger_model.py`: each new cell present→value / absent→blank (with the `Stand-in` defaulting-to-`passive` exception asserted); `_resolution_cell` present-zero `R 0 N 0` distinct from absent-blank; `columns` includes the three new headers in order (extend `test_columns_are_fixed_columns_then_metric_labels`); a heterogeneous old+new record mix flattens with `len(row) == len(columns)` and no `KeyError`; display-only (no writer / metric / detail-render change). Full `uv run pytest -q` green. **[Agent: testing]**
