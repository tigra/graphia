# Functional Specification: Play-As-Role via Environment Variable

- **Roadmap Item:** Developer affordance — adjacent to Phase 5 (Setup Flexibility) but narrower: pins the human's role within the existing fixed lineup. Does not change role counts.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today a player's role (Mafia or Law-abiding Citizen) is dealt at random from the fixed seven-card lineup — two Mafia, five Law-abiding. There's no way to choose which side you play; you take whatever the shuffle hands you. For someone repeatedly exercising the game — especially the author testing Mafia-only flows (night kills, private intros) versus Citizen-only flows — the random draw is friction: you relaunch until the seed happens to deal you the side you want.

This adds an optional setting, the `GRAPHIA_ROLE` environment variable, that pins the human's role for the session. It is read the same way as the other Graphia settings (from the environment / `.env`), which means it composes cleanly with the existing `make play` / `make play-remote` launch flow without threading a new command-line flag through the Makefile. The rest of the lineup fills the remaining cards as usual, the overall composition is unchanged, and (when a seed is set) the full assignment stays reproducible. When the variable is unset, behaviour is exactly as today — a random draw.

**Success looks like:** A developer launches with `GRAPHIA_ROLE=mafia` (via `.env`, an inline env var, or a `make play` passthrough), and the role-reveal at game start always tells them they are a Mafioso — no relaunch lottery. Setting `GRAPHIA_ROLE=law-abiding` always seats them on the Law-abiding side. Leaving it unset deals randomly, exactly as before.

---

## 2. Functional Requirements (The "What")

### 2.1 `GRAPHIA_ROLE` pins the human's role

- **As a** developer launching Graphia, **I want to** set `GRAPHIA_ROLE` to `mafia` or `law-abiding` to choose which side I play, **so that** I can exercise a specific role without relaunching until the random deal cooperates.
  - **Acceptance Criteria:**
    - [ ] With `GRAPHIA_ROLE=mafia`, the human is always seated as a Mafioso; the role-reveal message at game start reads "Your role is Mafia." (matching the existing reveal wording).
    - [ ] With `GRAPHIA_ROLE=law-abiding`, the human is always seated as a Law-abiding Citizen; the reveal reads "Your role is Law-abiding Citizen."
    - [ ] The value is case-insensitive: `MAFIA`, `Mafia`, `mafia` all work identically.
    - [ ] Only `mafia` and `law-abiding` are accepted. Any other value (e.g. `citizen`, `villain`, empty string) makes the program refuse to start and print an error that names the two valid choices.

### 2.2 Composition and the rest of the lineup are unchanged

- **As a** developer, **I want** forcing my role to NOT change the overall game balance, **so that** the game I'm testing is the same game everyone else plays — just with my seat pinned.
  - **Acceptance Criteria:**
    - [ ] The total lineup is still two Mafia and five Law-abiding, seven players, regardless of the setting.
    - [ ] When `GRAPHIA_ROLE=mafia`, the human takes one Mafia seat and the remaining six players are dealt the other one Mafia + five Law-abiding cards.
    - [ ] When `GRAPHIA_ROLE=law-abiding`, the human takes one Law-abiding seat and the remaining six players are dealt two Mafia + four Law-abiding cards.

### 2.3 Default behaviour unchanged when the variable is unset

- **As a** player who doesn't set the variable, **I want** the role deal to work exactly as it does today, **so that** the normal game is untouched.
  - **Acceptance Criteria:**
    - [ ] With `GRAPHIA_ROLE` unset, all seven roles are dealt at random, with the human's role unconstrained.
    - [ ] No new prompt, message, or visible change appears in the default (unset) launch.

### 2.4 Convenient launch via `make play`

- **As a** developer who launches through the Makefile, **I want to** set the role without editing `.env` every time, **so that** trying a role is a one-liner.
  - **Acceptance Criteria:**
    - [ ] `make play ROLE=mafia` (and `make play-remote ROLE=mafia`) launches the game with the human seated as Mafia; `ROLE=law-abiding` seats them Law-abiding.
    - [ ] Omitting `ROLE` from the `make play` invocation falls back to whatever `GRAPHIA_ROLE` is in `.env` (if anything), and if that too is unset, to the random default.

### 2.5 Tests pin the human's role via `GRAPHIA_ROLE`, not via magic seeds

- **As a** contributor writing or reading tests, **I want** role-dependent tests to pin the human's role by setting `GRAPHIA_ROLE` directly rather than by setting `GRAPHIA_SEED` to a magic value that happens to deal the desired role, **so that** the test's intent is self-documenting and decoupled from the RNG's behaviour.
  - **Acceptance Criteria:**
    - [ ] Existing tests that today reach for `SEED_MAFIA` / `SEED_LAW_ABIDING`-style constants solely to control the human's role pin via `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` / `"law-abiding"` instead. `GRAPHIA_SEED` is retained only where the test asserts mechanical-RNG behaviour beyond role assignment (speech order, vote tie-breaks, mafia-pointing target choice).
    - [ ] The `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constants are removed from every test file *except* the one whose explicit subject is the unset-path seed-→role mapping (`tests/test_slice4_role_reveal.py`), where the constants document today's RNG behaviour and the parametrised cases are the regression-guard.
    - [ ] `uv run pytest -q` stays green across the migration.

### 2.6 Mechanical-RNG-dependent tests pin behaviour via monkeypatched helpers, not via `GRAPHIA_SEED`

- **As a** contributor writing or reading tests that need a specific day-speech order or vote-call order, **I want** the test to express that need by monkeypatching the production helper that performs the shuffle, rather than by setting `GRAPHIA_SEED` to a value that incidentally produces the desired order, **so that** the test's intent is self-documenting and decoupled from how the seed flows through `assign_roles` / `night.py` / `day.py`.
  - **Acceptance Criteria:**
    - [ ] After this slice, only two test files set `GRAPHIA_SEED` for any purpose: `tests/test_dual_mode_smoke.py` (the deliberate cross-mode byte-equality test, where seeded determinism IS the test's subject) and `tests/test_slice4_role_reveal.py` (the seed-→role-mapping regression guard, where the parametrisation IS the test's subject).
    - [ ] Tests in `tests/test_slice6_day.py` and `tests/test_slice7_vote.py` that currently pin day-speech order or vote-call order via `GRAPHIA_SEED` use `monkeypatch.setattr(graphia.nodes.day, "_shuffle_order", ...)` (or equivalent) instead. The renamed-descriptive constants introduced during §2.5 (`SEED_HUMAN_MID_DAY_ORDER`, `SEED_DAY1_SPEAKER_ORDER_LETS_AI_INITIATE_VOTE`, `SEED_AMBIGUOUS_IA_PAIR_ALIVE`) are removed.
    - [ ] Production code (`src/graphia/config.py`, `src/graphia/nodes/day.py`, `src/graphia/nodes/night.py`) is unchanged — `GRAPHIA_SEED` and `config.seed` remain as today because `tests/test_dual_mode_smoke.py` still depends on them.
    - [ ] `uv run pytest -q` stays green across the migration.

### 2.7 Retire `GRAPHIA_SEED` from production; pin only the dual-mode test via `random.seed(...)`

- **As a** contributor reasoning about Graphia's runtime, **I want** the codebase to carry no `GRAPHIA_SEED` env var, no `config.seed` dataclass field, and no per-call seed-salt arithmetic, **so that** the only consumer of mechanical-RNG determinism (the cross-mode byte-equality test in `tests/test_dual_mode_smoke.py`) is the only place that talks about seeding, expressed locally via `random.seed(...)` rather than via an env-var protocol that surfaces nowhere else.
  - **Acceptance Criteria:**
    - [ ] `GraphiaConfig` no longer has a `seed: int` field; `load_config()` no longer reads or validates `GRAPHIA_SEED`.
    - [ ] `src/graphia/nodes/setup.py::assign_roles`, `src/graphia/nodes/day.py::_shuffle_order` (drops its `seed` parameter), and `src/graphia/nodes/night.py` (per-Mafioso pointing fallback, fallback round, kill-list tie-break) all use the module-global `random` (`random.shuffle`, `random.choice`) instead of constructing `random.Random(seed + offset)` instances.
    - [ ] `tests/test_dual_mode_smoke.py` pins its byte-equality trajectory by calling `random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)` once at the start of each mode's run, not via `monkeypatch.setenv("GRAPHIA_SEED", ...)`. Both modes share the same Python process and call production's `random`-using helpers in identical sequence, so byte-equality is preserved.
    - [ ] `tests/test_slice4_role_reveal.py` is deleted — its subject (seed-→role mapping) no longer exists.
    - [ ] `tests/test_play_as_role.py::test_default_unset_unchanged_under_seed` is deleted — its subject (byte-identity under a specific seed value) no longer exists.
    - [ ] `tests/test_play_as_role.py::test_role_value_is_case_insensitive` is refactored to pin cross-parametrize RNG via `random.seed(0)` (or an equivalent mechanism inside the test) rather than via `GRAPHIA_SEED`.
    - [ ] Stub helpers in `tests/test_slice6_day.py`, `tests/test_slice7_vote.py`, and `tests/test_vote_validation.py` that today match production's `_shuffle_order(players, seed)` signature with `(players, *_)` are simplified to `(players)` after production's signature change.
    - [ ] `tests/test_remote_mode_smoke.py`'s 3 direct `GraphiaConfig(..., seed=0, ...)` constructions drop the `seed=0` kwarg.
    - [ ] The autouse `monkeypatch.delenv("GRAPHIA_SEED", raising=False)` in `tests/conftest.py` is removed (no env var to clear).
    - [ ] After this slice, `grep -rn 'GRAPHIA_SEED' --include='*.py'` returns zero hits across the whole repository, and the running game's behaviour is exactly as today minus reproducibility under a seed.
    - [ ] `uv run pytest -q` stays green across the refactor; `tests/test_dual_mode_smoke.py` still passes (byte-equality preserved by the in-test `random.seed`).

---

## 3. Scope and Boundaries

### In-Scope

- A single setting, `GRAPHIA_ROLE`, read from the environment / `.env`, accepting exactly `mafia` or `law-abiding` (case-insensitive).
- Pinning the human's role while preserving the fixed 2-Mafia / 5-Law-abiding composition.
- An error-and-refuse-to-start path for invalid values.
- A `ROLE=` passthrough on the `make play` / `make play-remote` targets.
- Migrating the existing test suite from magic-seed role-pinning to `GRAPHIA_ROLE`-based role-pinning, retaining `GRAPHIA_SEED` only for genuine mechanical-RNG determinism.

### Out-of-Scope

- A `--role` command-line flag (deliberately not added — the env var composes better with the Makefile launch flow).
- **Configurable role counts** (asking how many Mafia / Citizens) — that's Phase 5 (Setup Flexibility), a separate spec.
- Forcing roles for AI players, or forcing a *specific other player* to be Mafia.
- Any in-game UI for choosing a role (this is launch-time only).
- New roles beyond Mafia / Law-abiding (Phase 8 Expanded Roster).
- Changing the role-reveal message wording or the private Mafia-intro flow.
- All other remaining roadmap items.
what