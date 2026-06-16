---
spec: 014-configurable-role-counts
spec_title: Configurable Role Counts
introduced_on: 2026-06-16
---

# Concepts introduced in this increment

## Invariants

- **One config drives a size invariant** (`single-source-size-invariant`) — The role-deck size (`num_mafia + num_citizens`) and the AI-roster size (`1 human + total − 1 names`) both derive from one configured lineup, so they can't drift; a mismatch would `IndexError` in the role-mapping loop, which makes "they always agree" the spine the whole increment defends.

## Configuration & validation

- **Config-derived lineup with fail-fast validation** (`config-derived-lineup-fail-fast`) — Whole-table counts (human included) come from env vars parsed and validated in `load_config` *before* the TUI or an eval starts; each broken rule raises `SystemExit` naming the rule, never a stack trace. Extends the project's env-config-with-validation and fail-fast-before-the-game-starts posture.
- **A validation rule that encodes the game theory** (`parity-rule-as-validation`) — The startup guard enforces `num_mafia < num_citizens` because the win check already lets the Mafia win at parity; refusing parity-or-worse up front means the game can never be mathematically decided before it opens.

## Role assignment

- **Config-built role deck** (`config-built-role-deck`) — `assign_roles` builds the deck from the counts (`["mafia"] * m + ["law_abiding"] * c`) instead of a hardcoded seven, then deals randomly — preserving the random-shuffle path and the `GRAPHIA_ROLE` pop-then-prepend pin, with the human always dealt in within those counts.

## Structured output (robust counting)

- **A schema that bounds, a caller that pins** (`range-schema-caller-enforced-count`) — `Roster.names` relaxes from a fixed length to a `Field(min_length=1, max_length=_MAX_AI_NAMES)` range (still flat-Pydantic), and the *prompt* is parameterized by `{count}` — moving exact-count enforcement out of the schema and onto the caller.
- **A deterministic coerce floor under validation-retry** (`coerce-floor-under-retry`) — Beyond the project's single corrective retry, `_generate_names` adds a pure `_coerce_to_count` that trims or pads to *exactly* N distinct names, so a small model that can't reliably count never breaks the size invariant — the guarantee is structural, not best-effort.

## Measurement

- **Recording a controllable variable in the ledger** (`lineup-recorded-and-viewable`) — The lineup is recorded as `settings.lineup` in each eval run and surfaced in the viewer (a `Lineup` column + drill-down, defensive against pre-014 records), and `--citizens`/`--mafia` CLI flags route through the *same* config choke point — turning table size into a tracked, comparable eval dimension.
