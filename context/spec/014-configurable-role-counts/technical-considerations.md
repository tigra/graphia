# Technical Specification: Configurable Role Counts

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

The lineup becomes **config-driven** end to end, following the project's established `GRAPHIA_*` env + fail-fast-before-TUI pattern. Two new env vars (`GRAPHIA_NUM_CITIZENS` / `GRAPHIA_NUM_MAFIA`, whole-table counts incl. the human, **default 5 / 2** when unset) are parsed and **validated in `load_config()`** — an invalid lineup raises `SystemExit` with a clear message before the game (or the eval) starts. The role **deck** in `assign_roles` is then built from those counts instead of a hardcoded 7-element list, and the **roster name-generation** produces `total − 1` AI names instead of a fixed 6. Because the eval harness re-reads `load_config()` per game, the configured lineup flows through automatically; the harness records it in each run's `settings`, and the viewer shows it.

**The central invariant:** the deck size (`num_mafia + num_citizens`) and the roster size (`1 human + (total − 1) AI names`) both derive from the same config, so they can't drift — a mismatch would `IndexError` in the role-mapping loop, which the name-gen fallback (below) makes impossible.

**Two things need no change** (verified): `check_win_condition` (`nodes/endgame.py`) is already count-relative (`alive_mafia >= alive_law → mafia win`; no mafia → law win) — its parity-favours-mafia tie is exactly *why* validation enforces `num_mafia < num_citizens` at start; and the Mafia teammate-intro (`nodes/night.py`) already formats whatever mafia list it's given. **No schema change to `DayAction`/`Ballot`, no graph topology change.**

Primarily **`python-backend`** (config, setup node, the `Roster` schema + name prompt, the harness + viewer). No new dependency → no `/awos:hire`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Config: env vars + validation + fail-fast — `src/graphia/config.py` — **[Agent: python-backend]**

- New env vars **`GRAPHIA_NUM_CITIZENS`** / **`GRAPHIA_NUM_MAFIA`** (whole-table, human included); module constants `_DEFAULT_NUM_CITIZENS = 5`, `_DEFAULT_NUM_MAFIA = 2`, `_MAX_TABLE_SIZE = 12`. Two new `GraphiaConfig` fields `num_citizens: int = 5`, `num_mafia: int = 2` (defaulted so direct-construction tests stay valid).
- A `_parse_count(name, default)` helper (empty/unset → default; non-numeric → `SystemExit` naming the var). Validation block in `load_config` (after the provider block), each case → `SystemExit` with the rule named: **non-numeric**, **< 1 mafia** ("a game with no Mafiosos is already over"), **< 1 citizen**, **mafia ≥ citizens** ("the Mafia start at or above the parity that wins them the game before it begins"), **total > `_MAX_TABLE_SIZE`**. (Negative/zero are caught by the `< 1` guards.) Composes cleanly with the existing `GRAPHIA_ROLE` validation — both roles always have ≥1 seat, so a pinned human role always has a seat; no cross-guard needed.
- **The `_MAX_TABLE_SIZE = 12` cap is a deliberate, documented ceiling** (one constant, trivially raised): it keeps a full Day round (`total + 1` messages ≤ 13) well inside the `_CONTEXT_WINDOW = 30` so no speaker loses mid-round context; keeps the small model's one-shot name request modest (≤ 11 names); and bounds vote-poll cost/Bedrock tokens per game. *Flag:* if a future cap rise approaches `_CONTEXT_WINDOW`, the window must be re-derived from `total` rather than left at the literal 30 (out of scope now; the cap protects the current literal).

### 2.2 Role deck from config — `assign_roles` in `src/graphia/nodes/setup.py` — **[Agent: python-backend]**

- Replace the literal 7-element deck with `deck = ["mafia"] * config.num_mafia + ["law_abiding"] * config.num_citizens`. The random-shuffle path and the `config.human_role` pin path (`deck.remove(pinned) + prepend`, safe since both roles always have ≥1 seat) stay structurally identical; the insertion-order role-mapping loop (human at index 0) is unchanged.
- Preserve the human-at-index-0 assert; the contract `len(deck) == len(players)` holds because both equal `total` (see §2.3's fallback, which guarantees the roster is exactly `total − 1`).

### 2.3 Variable roster size — `Roster` schema + name-gen — `src/graphia/llm.py`, `prompts.py`, `setup.py` — **[Agent: python-backend]**

- **Relax `Roster`:** `names: list[str] = Field(min_length=1, max_length=_MAX_AI_NAMES)` where `_MAX_AI_NAMES = _MAX_TABLE_SIZE − 1` (still a flat primitive-list schema — Bedrock Converse + Ollama Anthropic-compat both fine). Keep the distinct/non-empty validator. Exact-count enforcement moves to the caller.
- **Parameterize the prompt:** `NAME_GEN_USER` → `NAME_GEN_USER_TEMPLATE` with `{count}`. (Verified: `blunder_eval` imports only `DAY_*`/`VOTE_*` templates, not `NAME_GEN_USER` — the rename is safe; only `setup.py` imports it.)
- **`_generate_names(count)` — validation-retry-then-trim/pad fallback** (the project's structured-output safety posture): invoke; if parsed and `len == count`, done; on `ValidationError` or wrong count, one corrective retry naming the exact count; if still wrong/raises, a pure **`_coerce_to_count(roster, count)`** trims (first `count` distinct) or pads with deterministically-distinct placeholder names — **so the roster is *always* exactly `count`**, never an `IndexError` in `assign_roles`. `generate_roster` computes `ai_count = num_citizens + num_mafia − 1` and passes it.

### 2.4 Record the lineup in eval + show in viewer — `blunder_eval.py`, `eval_ledger.py` — **[Agent: python-backend]**

- **Recording (config-driven):** add a nested `lineup: {num_citizens, num_mafia}` sub-map to the `settings` dict in `run_eval` (read off the resolved `config`), rendered after the flat settings keys via the existing one-level nested-map render path; update the `render_record` `settings:` shape docstring.
- **Optional eval CLI override (recommended):** `--citizens` / `--mafia` flags that set the env vars *before* `load_config()` (mirrors `_apply_model_overrides`), so the same fail-fast validation runs and a maintainer can sweep lineups without editing `.env`. Pure convenience — recording works without it; env stays the single source of truth.
- **Viewer:** add `citizens`/`mafia` lines to `_render_settings_section` via the defensive `_dig` (pre-014 records → `—`, no migration); add a compact **`Lineup`** fixed column (`_lineup_cell` → `"5/2"` or blank when absent) placed in the head block before `Notes` so the metric right-justify split (`len(columns) − len(METRIC_ORDER)`) is undisturbed (the spec wants it "surfaced in the table where it fits"). Optional `"5c2m"`-style search-blob keyword.

---

## 3. Impact and Risk Analysis

- **Blast radius:** `config.py` (+2 fields, validation), `setup.py` (deck + name-gen fallback), `llm.py` (Roster range), `prompts.py` (name prompt template), `blunder_eval.py` + `eval_ledger.py` (lineup recording/display), tests. **No game-flow/graph/schema change.**
- **Risk — deck/roster size drift → `IndexError`.** *Mitigation:* both derive from one config; `_coerce_to_count` guarantees the roster is exactly `total − 1`, so `len(deck) == len(players)` always.
- **Risk — small/local model returns the wrong name count.** *Mitigation:* the validation-retry-then-trim/pad fallback (§2.3) — the game always proceeds with exactly the right number of distinct names.
- **Risk — table size outgrows fixed assumptions** (`_CONTEXT_WINDOW = 30`; the eval per-interrupt budget `max_rounds*12 + 20` sized for 7 players). *Mitigation:* the `_MAX_TABLE_SIZE = 12` cap keeps a full round inside the window (≤13 ≤ 30); **flag** that the eval driver's per-interrupt budget should be re-derived ~`total + headroom` if large-lineup eval runs are used (low risk at default; tied to the `--citizens/--mafia` overrides), and that `_CONTEXT_WINDOW` must scale with `total` if the cap ever rises.
- **Determinism unchanged (architecture §6):** deck shuffle + name-gen use module-global `random` / mocked `get_small`; no seed env. The new validation is pure config parsing.

---

## 4. Testing Strategy — **[Agent: testing]**

- **New offline tests:** (1) **config validation** (`tests/test_lineup_config.py`, mirroring `test_llm_provider_config.py`) — each invalid case (`NUM_MAFIA=0`/`abc`/`-1`, mafia≥citizens, total>cap) → `pytest.raises(SystemExit)` with the rule named; unset → `(5,2)`; valid custom → parsed onto the fields. (2) **deck composition** — call `assign_roles` with `monkeypatch`-set counts + a synthetic players map: exactly `num_mafia`/`num_citizens` dealt, human at index 0, `len(deck)==len(players)`; plus the `GRAPHIA_ROLE` pin path on a non-default lineup. (3) **`_coerce_to_count`** (pure) — too-many→trim, too-few/`None`→pad, all distinct; and a `fake_small`-driven `_generate_names` test — wrong count → retry → success (call_count 2), retry-still-wrong → fallback coerces to N.
- **Tests to update (the variable-count fallout):** `tests/test_slice3_names.py` — its retry trigger `Roster(names=["only-one"])` **stops raising** once the schema range is relaxed; change the trigger to `[]` or a duplicate-name list, and add the wrong-count retry test. `tests/conftest.py` `fake_small`/`FakeSmall` — docstrings only ("6 names" → "the lineup's AI-name count"); the fixture already wraps any-length list. `tests/test_slice_day_context_window.py` — add a guard that `_CONTEXT_WINDOW ≥ _MAX_TABLE_SIZE + 1` (protects the cap justification).
- **Default-lineup tests unchanged:** with env unset the default stays 5+2 = 7, so `test_slice2_roster.py`, `test_slice5_night.py`, `test_slice8_endgame.py`, `test_play_as_role.py`, the day-order/fairness/quit/abandoned tests keep passing (assert the count follows from default config rather than a bare literal where cheap).
- `safe_llm` untouched (`_generate_names` stays in `setup.py`); no real model in the suite.
