# Technical Specification: Play-As-Role via Environment Variable

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Completed
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

**Constant cleanup.** Once each migrated file no longer references its `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constant, delete the constant. Any constant retained for documenting a non-role-pinning role of the seed must be renamed to describe what it actually pins (e.g., `SEED_NIGHT_POINTING_DETERMINISTIC`). Slice 4 then removes these renamed constants too, by migrating the underlying tests from seed-pinning to monkeypatch-pinning.

### 2.7 Monkeypatch-the-helper migration (functional-spec §2.6)

The 5 test sites that survive §2.6 with renamed descriptive constants — currently `SEED_HUMAN_MID_DAY_ORDER` in `tests/test_slice6_day.py` and `SEED_DAY1_SPEAKER_ORDER_LETS_AI_INITIATE_VOTE` / `SEED_AMBIGUOUS_IA_PAIR_ALIVE` in `tests/test_slice7_vote.py` — all pin the same underlying production behaviour: the day-speech-order shuffle in `src/graphia/nodes/day.py`. That shuffle already lives in a named module-level helper, `_shuffle_order(players, seed) -> list[str]`, so the migration is purely a test-side change with no production refactor.

**Migration pattern, per call site:**

- Today: `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_<DESCRIPTIVE_NAME>))` + a comment explaining the pinned behaviour.
- After: `monkeypatch.setattr(graphia.nodes.day, "_shuffle_order", lambda players, seed: <hand-written-order>)` where `<hand-written-order>` is the deterministic list of alive-player-ids the test needs. The seed setenv goes away entirely; the renamed constant goes away.

**Hand-written-order construction.** Each test knows what shape it needs:
- `test_slice6_day.py::test_day_rounds_shuffle_and_players_speak` needs the human at index ≥3 in the alive-id order so at least 3 AI speakers precede the human's day_turn. The monkeypatch's lambda returns `[ai_ids[0], ai_ids[1], ai_ids[2], human_id, ai_ids[3], ai_ids[4], ai_ids[5]]` (or any order that places the human at or after index 3).
- The 3 sites in `test_slice7_vote.py` that today pin `SEED_DAY1_SPEAKER_ORDER_LETS_AI_INITIATE_VOTE` need the first speaker to NOT be the AI Mafia target (so scripted vote-DayActions don't get rejected as self-targeting). The lambda places a Law-abiding AI first.
- The 1 site that pins `SEED_AMBIGUOUS_IA_PAIR_ALIVE` needs both Bianca and Elias alive after Night 1 (the "ia" prefix is ambiguous between them). The monkeypatch's scope is broader — it pins the Night-1 victim choice, which today happens via the mafia-pointing seeded RNG in `src/graphia/nodes/night.py`, not via `_shuffle_order`. That site needs a different patching surface (probably the per-Mafioso pointing RNG helper in `night.py`, or the `target_human_pointing`-style fixture that already exists in `tests/conftest.py`).

**Production code unchanged.** `GRAPHIA_SEED`, `config.seed`, and the seeded `random.Random(...)` calls in `night.py` / `day.py` all stay. `tests/test_dual_mode_smoke.py` is the legitimate user of the seed for byte-equal cross-mode parity (see ADR-006 "Test role-pinning convention" for the principle; this slice extends the same posture to the mechanical-RNG layer, with the dual-mode test as the sanctioned exception). Architecture §6 "Determinism Posture & Testing Conventions" bullet 3 already names this monkeypatching pattern as the project's testing convention; this slice is its concrete instantiation for the remaining 5 sites.

### 2.8 Retire `GRAPHIA_SEED` from production (functional-spec §2.7)

After slices 1–4 land, `GRAPHIA_SEED` and `config.seed` exist solely for one test (`tests/test_dual_mode_smoke.py`). The production code carries an env var, a config field, and a salt-arithmetic pattern at every shuffle call site, all in service of one cross-mode byte-equality assertion. Slice 5 removes the seed mechanism from production and moves its determinism narrative entirely inside that one test.

**Production refactor:**

- `src/graphia/config.py`:
  - Remove the `seed: int` field from `GraphiaConfig`.
  - Remove the `GRAPHIA_SEED` parsing block from `load_config()` (the `seed_raw` lookup, the int-coercion, the `time.time_ns()` fallback, and the error-printing branch).
  - Remove the `seed=…` kwarg from the `GraphiaConfig(...)` constructor call.
- `src/graphia/nodes/setup.py::assign_roles`:
  - Replace `rng = random.Random(config.seed); rng.shuffle(deck)` with `random.shuffle(deck)` (module-global).
  - Same for both branches (unset path and pinned path).
- `src/graphia/nodes/day.py::_shuffle_order`:
  - Drop the `seed` parameter; signature becomes `_shuffle_order(players: dict[str, PlayerState]) -> list[str]`.
  - Body: `ids = [...]; random.shuffle(ids); return ids` — uses module-global RNG.
  - Drop the docstring's "deterministic per (cycle, round)" claim.
- `src/graphia/nodes/day.py` call sites (around lines 162, 291, 308, 398, 633): remove the `order_seed` / `next_seed` salt-arithmetic; call `_shuffle_order(players)` with one arg.
- `src/graphia/nodes/night.py` (around lines 139, 180, 212): replace `random.Random(seed + offset)` with direct `random.choice(...)` / `random.shuffle(...)` against the module-global RNG. The "Mafia pointing fallback" and "kill-list tie-break" sites are the most-relevant.

**Test refactor for `tests/test_dual_mode_smoke.py`:**

The byte-equality assertion (`local_result["public_log"] == remote_result["public_log"]`, etc.) relies on identical RNG draws across the two mode runs. Today this is achieved by setting `GRAPHIA_SEED=0` so each mode's production code re-derives identical per-call seeds from `config.seed=0`. After Slice 5, production no longer reads `GRAPHIA_SEED`; the test pins determinism directly by calling `random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)` once at the start of each mode's run (before the first call into the production graph).

Both modes share the same Python process; both call the same module-global `random.shuffle(...)` / `random.choice(...)` calls in the same order (same LangGraph topology). After `random.seed(0)`, the next N calls into the module-global RNG produce a deterministic sequence; re-calling `random.seed(0)` at the start of the second mode reproduces that same sequence. Byte-equality is preserved.

**Test deletions and refactors:**

- `tests/test_slice4_role_reveal.py`: delete entirely. Its parametrised "seed N → role X" assertion is the test's whole subject; with seeds gone there is nothing left to verify. Functional intent of the test (role-reveal lands private, not public) is already covered by other slice tests.
- `tests/test_play_as_role.py::test_default_unset_unchanged_under_seed`: delete. The unset path is now intentionally non-deterministic; there is no "expected list" to assert against.
- `tests/test_play_as_role.py::test_role_value_is_case_insensitive`: refactor. The cross-parametrize identity assertion currently depends on `GRAPHIA_SEED="0"` producing identical role assignments across three runs of `MAFIA` / `Mafia` / `mafia`. Replace with one of:
  - Per-variant `random.seed(0)` before each `assign_roles` call so the module-global RNG produces the same sequence each time.
  - Restructure the assertion: just verify each variant of the env-var value parses to the same `config.human_role` internal token via `load_config()` directly (no need to drive through `assign_roles`).
  - The second form is cleaner — it tests the case-insensitivity at the parsing layer, which is where it lives.
- `tests/test_remote_mode_smoke.py`: drop `seed=0` from the 3 direct `GraphiaConfig(...)` constructions (lines ~739, ~1017, ~1130).
- Stub helpers in `tests/test_slice6_day.py`, `tests/test_slice7_vote.py`, `tests/test_vote_validation.py`: `(players, *_)` → `(players)`. Five sites total.
- `tests/conftest.py:268`: delete the autouse `monkeypatch.delenv("GRAPHIA_SEED", raising=False)` line and its accompanying docstring fragment.

**Documentation updates:**

- `context/spec/005-play-as-role/functional-spec.md` §2.3 "Default behaviour unchanged when the variable is unset": drop the parenthetical "(seeded by `GRAPHIA_SEED` as today)" from the unset-path acceptance criterion.
- `context/product/architecture.md` §6 "Determinism Posture & Testing Conventions" bullet 3: tighten the wording — the "Tests that need a specific mechanical outcome pin it via targeted monkeypatching" sentence loses its implicit "(except for the dual-mode test, which uses `GRAPHIA_SEED`)" caveat. The dual-mode test's mechanism is now `random.seed(...)` inside the test body, which still matches the "targeted monkeypatching" framing in spirit (it's a test-local determinism pin, not a production-code env-var protocol).
- `context/adr/006-test-role-pinning-via-graphia-role.md` §3 Decision: amend to note that the "single deliberate exception is `tests/test_slice4_role_reveal.py`" sentence is obsolete (file deleted in Slice 5).

**Sequencing.** Production refactor (`_shuffle_order`, `assign_roles`, night.py, config.py) and the dual-mode-test pin must land together — production changes break the dual-mode test until the test's pin moves from env-var to `random.seed`. Treat as one atomic sub-task pair. Test deletions and stub-signature simplifications follow.

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