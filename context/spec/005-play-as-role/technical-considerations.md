# Technical Specification: Play-As-Role via Environment Variable

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Pinning the human's role is implemented as a thin, deterministic skew on top of the existing role-assignment path. The work is concentrated in three files plus the Makefile:

1. **`src/graphia/config.py`** — read and validate `GRAPHIA_ROLE` alongside the other Graphia env vars. Invalid values raise `SystemExit` with a message naming the two valid choices (`mafia`, `law-abiding`). Unset → `None` → today's behaviour.
2. **`src/graphia/nodes/setup.py::assign_roles`** — when `config.human_role` is set, pop one card of that role from the fixed 7-card deck, shuffle the remaining six with the seeded RNG, and place the pinned role at position 0 (the human) followed by the six shuffled cards (the AIs, in `players` insertion order). When `config.human_role` is `None`, the function behaves exactly as today.
3. **`src/graphia/__main__.py`** — call `load_config()` once at startup so an invalid `GRAPHIA_ROLE` aborts before the Textual app captures the terminal (consistent with how the remote-mode env-var check already fails fast inside `load_config()`).
4. **`Makefile`** — add a `ROLE=` passthrough to the `play` and `play-remote` targets so `make play ROLE=mafia` sets `GRAPHIA_ROLE=mafia` for the spawned Python process only.

The composition (2 Mafia + 5 Law-abiding, 7 seats) is preserved by construction: the deck size and contents are fixed; we just remove one card before shuffling, then re-insert it at index 0.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Config layer (`src/graphia/config.py`)

- **New field on `GraphiaConfig`:** `human_role: str | None` — internal token, either `"mafia"`, `"law_abiding"`, or `None`. (Internal token uses the underscore form to match the existing `PlayerState.role` values; the env var accepts the user-friendly `law-abiding`.)
- **New parser in `load_config()`:**
  - Read `os.environ.get("GRAPHIA_ROLE")`.
  - If unset or pure whitespace → `human_role = None`.
  - Else strip + lowercase the value and map: `"mafia"` → `"mafia"`, `"law-abiding"` → `"law_abiding"`.
  - Any other non-empty value (including empty-string after env explicitly set to `""`) → `raise SystemExit("GRAPHIA_ROLE must be 'mafia' or 'law-abiding' (got {value!r}).")`.
- Field is added to the `GraphiaConfig` constructor call at the end of `load_config()`. No other config field changes.

### 2.2 Role assignment (`src/graphia/nodes/setup.py::assign_roles`)

The existing function builds a deck `["mafia", "mafia", "law_abiding"×5]`, shuffles with `random.Random(config.seed)`, and assigns cards to players in `players` insertion order.

Modify it as follows:

- Read `config.human_role`.
- Identify the human's player id (`state["human_id"]`) and its position in `state["players"]` (it is always position 0 by construction — `collect_name` inserts the human before `generate_roster` adds AIs). Assert this invariant defensively; if it ever ceases to hold, fail loudly rather than silently mis-assigning.
- **Unset path (`human_role is None`)** — unchanged: shuffle the full 7-card deck and assign by iteration order.
- **Pinned path** — pop one card matching `human_role` from the deck (leaving six cards: one Mafia + five Law-abiding if the human pinned Mafia; two Mafia + four Law-abiding if the human pinned Law-abiding). Shuffle the six remaining cards with the seeded RNG. The final per-position assignment is `[human_role, *shuffled_six]`.
- Whichever path runs, the shape of the return value is unchanged: `{"players": {...full updated dict...}}`.

**Unset-path safety.** When `config.human_role is None`, the function returns the same bytes as today's implementation — the new code branches only when a role is pinned. This is what keeps the existing `tests/test_slice4_role_reveal.py` (which parametrises seeds 0 → Law-abiding, 3 → Mafia) passing without modification.

### 2.3 Startup fail-fast (`src/graphia/__main__.py`)

After `load_dotenv()` and the arg-parse + `GRAPHIA_REMOTE` env coercion, and **before** `GraphiaApp().run()`, call `load_config()` once and discard the result. This pulls the existing `SystemExit` paths (invalid `GRAPHIA_ROLE`; remote-mode without `GRAPHIA_RUNTIME_URL`) up to the terminal *before* the Textual app installs its alternate screen buffer. No other behavioural change.

### 2.4 Makefile passthrough (`Makefile`, targets `play` and `play-remote`)

Add a `ROLE` make-variable passthrough on the two `play` targets:

```makefile
play:
    $(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )uv run python -m graphia $(ARGS)

play-remote:
    $(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )uv run python -m graphia --remote $(ARGS)
```

- `make play ROLE=mafia` → `GRAPHIA_ROLE=mafia uv run …` (env scoped to the child process only; `.env` and shell env untouched).
- `make play` (no `ROLE`) → unchanged: `uv run …` inherits `GRAPHIA_ROLE` from `.env` / shell, if any.
- `ROLE=law-abiding` is honoured the same way. Validation of the value itself stays in Python (`load_config`) so the Makefile is dumb about what's valid — keeps a single source of truth.

### 2.5 No other production code changes

The role-reveal wording in `reveal_role` already produces "Your role is Mafia." / "Your role is Law-abiding Citizen." from the existing `_ROLE_LABELS` mapping. The Mafia-intro flow, the night/day logic, win-condition detection — none of them care *how* a role got assigned, only what it is. They need no changes.

### 2.6 Test-suite migration (functional-spec §2.5)

The existing test suite uses module-level `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constants and `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_*))` to deal a particular role to the human by picking a seed that happens to do so. With `GRAPHIA_ROLE` available, every such usage migrates to `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` (or `"law-abiding"`) plus a stable, intent-free seed value (`0`).

**In-scope test files (replace magic-seed role-pinning with `GRAPHIA_ROLE`):**

- `tests/test_dual_mode_smoke.py`
- `tests/test_remote_mode_smoke.py`
- `tests/test_slice5_night.py`
- `tests/test_slice6_day.py`
- `tests/test_slice7_vote.py`
- `tests/test_slice8_endgame.py`
- `tests/test_slice9_polish.py`
- `tests/test_vote_validation.py`
- `tests/test_quit_modal.py`
- `tests/test_vote_driver.py` (uses a single inline `SEED` constant; rename and recategorise as part of the sweep)

**Out-of-scope test file (keep magic seeds — they are the test's subject, not a workaround):**

- `tests/test_slice4_role_reveal.py` — this file parametrises specifically on `(SEED_LAW_ABIDING=0, "Law-abiding Citizen", ...)` and `(SEED_MAFIA=3, "Mafia", ...)` to assert today's unset-path seed-→role mapping. The constants document RNG behaviour; the parametrised cases are the regression-guard for §2.3 (`Default behaviour unchanged when the variable is unset`). Leave it untouched.

**Migration pattern, per call site:**

- Today: `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_MAFIA))`
- After: `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` — the seed setenv is **dropped entirely** by default. `tests/conftest.py` unsets `GRAPHIA_SEED` for every test that does not opt in, so the absence of an explicit setenv lands the test on a time-based seed, which is the existing convention for role-pinning-only tests.

**Identifying load-bearing seeds beyond role-pinning.** For each migration site, inspect the surrounding test for assertions that depend on speech order, vote order, mafia-pointing target identity, or tie-break outcomes. If any are found, **reintroduce** an explicit `monkeypatch.setenv("GRAPHIA_SEED", "<value>")` alongside the new `GRAPHIA_ROLE` setenv, with an inline comment explaining what specific RNG behaviour the value pins (e.g., `# seed 0 makes Day-2 speech start with <name>; tie-break test below depends on this`). The reintroduced constant is *renamed* to describe the RNG behaviour it pins (e.g., `SEED_NIGHT_POINTING_DETERMINISTIC`), never `SEED_MAFIA` / `SEED_LAW_ABIDING` (those names lie about what the seed is for). The default rule remains: no `GRAPHIA_SEED` setenv unless absolutely necessary.

**Constant cleanup.** Once each migrated file no longer references its `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constant, delete the constant. Any constant retained for documenting a non-role-pinning role of the seed must be renamed to describe what it actually pins (e.g., `SEED_NIGHT_POINTING_DETERMINISTIC`).

---

## 3. Impact and Risk Analysis

### System Dependencies

- **`PlayerState.role` literal type** — already supports `"mafia"` and `"law_abiding"`; no schema change.
- **`config.seed`** — we reuse the same `random.Random(config.seed)` instance for the deck shuffle. After the test migration in §2.6, the seed is consumed only by the production code that genuinely needs mechanical determinism (night pointing, day speech order, tie-breaks); tests stop conflating "I want the human to be Mafia" with "I want a specific RNG draw".
- **Player insertion order** — the human is always the first key inserted into `state["players"]`. The assertion at the top of the pinned path guards this; if a future refactor changes the order, the test suite will fail visibly rather than silently mis-seat the human.

### Potential Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Invalid `GRAPHIA_ROLE` swallowed by the Textual alternate screen, making the error invisible. | Call `load_config()` in `__main__` before `GraphiaApp().run()`; the `SystemExit` lands on stderr in the normal terminal buffer (same pattern as the existing remote-mode check). |
| Future refactor decouples player insertion order from "human first" and silently mis-seats the human. | Defensive `assert state["human_id"] == next(iter(state["players"]))` (or equivalent) at the top of the pinned path. Backed by a deterministic test that compares both paths' outputs under a fixed seed. |
| Seed determinism broken because the pinned path consumes the RNG differently from the unset path. | Acceptable — the unset path is byte-for-byte identical to today (§2.2 implementation: unset → original `assign_roles` body), so all existing seed-→role tests in `tests/test_slice4_role_reveal.py` continue to pass. Pinned-path determinism is not a stated requirement. |
| Make's `$(if …)` quoting eats spaces or quotes. | The construct emits either an empty string or `GRAPHIA_ROLE=<value> ` (trailing space inside the `$(if)`). Validate via a smoke `make play ROLE=mafia ARGS="--help"` invocation; the Python-side `load_config()` is the authoritative validator anyway. |
| Test migration in §2.6 silently drops mechanical-RNG assertions a test was secretly relying on (e.g., a tie-break that happens a certain way under seed 0 vs seed 3). | Per-call-site review per the §2.6 "Identifying load-bearing seeds" checklist before deleting any `GRAPHIA_SEED` setenv. Final guard: `uv run pytest -q` green across the migration. If a test fails, restore the seed with a renamed constant whose name explains why the seed value matters. |

---

## 4. Testing Strategy

All tests are unit/integration-level pytest under `tests/`; no AWS or LLM calls. New file: **`tests/test_play_as_role.py`**.

Mock surface: `assign_roles` and `load_config` are pure — pass a constructed `GameState` and patch `os.environ` (or use `monkeypatch.setenv`). The autouse `safe_llm` fixture continues to block any accidental Bedrock call.

Cases (one test function each):

- `test_default_unset_unchanged_under_seed` — with `GRAPHIA_SEED=1234`, no `GRAPHIA_ROLE`: the full assignment matches a frozen expected list (regression-guard for §2.3 "Default behaviour unchanged when the variable is unset"; confirms the unset path is byte-identical to today's `assign_roles`).
- `test_pin_mafia_seats_human_as_mafia` — `GRAPHIA_ROLE=mafia`: human's role is `"mafia"`; AI roles contain exactly one `"mafia"` and five `"law_abiding"` (§2.1, §2.2).
- `test_pin_law_abiding_seats_human_as_law_abiding` — `GRAPHIA_ROLE=law-abiding`: human is `"law_abiding"`; AIs contain two `"mafia"` + four `"law_abiding"` (§2.1, §2.2).
- `test_role_value_is_case_insensitive` — `MAFIA`, `Mafia`, `mafia` all produce the same assignment (§2.1).
- `test_invalid_role_value_exits_with_message` — `GRAPHIA_ROLE=villain` → `SystemExit`, captured stderr names both valid choices (§2.1 final criterion).
- `test_reveal_role_message_unchanged` — light smoke through `reveal_role` to confirm the existing wording ("Your role is Mafia." / "Your role is Law-abiding Citizen.") still appears under the pinned path (§2.1).

Acceptance criterion §2.4 "Convenient launch via `make play`" is verified manually — the Makefile change is a one-line passthrough and `make`'s `$(if …)` semantics are not worth a subprocess test.

Acceptance criterion §2.5 "Tests pin the human's role via `GRAPHIA_ROLE`, not via magic seeds" is verified by the full pytest suite staying green across the migration; the migration is itself a test-suite refactor with no new test coverage being added.