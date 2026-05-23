# Tasks: Play-As-Role via Environment Variable

- **Functional Specification:** `context/spec/005-play-as-role/functional-spec.md`
- **Technical Considerations:** `context/spec/005-play-as-role/technical-considerations.md`
- **Status:** Draft

## Slice 1 — Pin human's role via `GRAPHIA_ROLE` (both sides)

After this slice, setting `GRAPHIA_ROLE=mafia` or `GRAPHIA_ROLE=law-abiding` (case-insensitive) reliably seats the human on that side at every launch; any other non-empty value refuses to start with an error naming the two valid choices. The composition stays 2-Mafia/5-Law-abiding. The unset path is byte-identical to today.

- [ ] **Sub 1.1:** Extend `GraphiaConfig` (`src/graphia/config.py`) with `human_role: str | None`. In `load_config()`, read `GRAPHIA_ROLE`, strip + lowercase. Map `"mafia"` → `"mafia"`, `"law-abiding"` → `"law_abiding"`. Unset / whitespace-only → `None`. Any other non-empty value → `raise SystemExit("GRAPHIA_ROLE must be 'mafia' or 'law-abiding' (got {value!r}).")`. Pass `human_role` through the `GraphiaConfig` constructor. **[Agent: python-backend]**
- [ ] **Sub 1.2:** In `src/graphia/nodes/setup.py::assign_roles`, branch on `config.human_role`. When `None`: existing behaviour unchanged. When set: `assert state["human_id"] == next(iter(state["players"]))` (defensive guard for the human-first invariant), pop one card matching the pinned role from the 7-card deck, shuffle the remaining six with `random.Random(config.seed)`, and assign `[pinned_role, *shuffled_six]` to the players in iteration order. Return shape unchanged. **[Agent: langgraph-agentic]**
- [ ] **Sub 1.3:** In `src/graphia/__main__.py`, call `load_config()` after `load_dotenv()` + the `args.remote` env coercion, before `GraphiaApp().run()`. Discard the result — the call exists solely to surface `SystemExit` (invalid `GRAPHIA_ROLE`; remote-mode missing `GRAPHIA_RUNTIME_URL`) on stderr before the Textual alternate screen captures the terminal. **[Agent: python-backend]**
- [ ] **Sub 1.4:** Create `tests/test_play_as_role.py`. Tests (each constructing the post-`generate_roster` `GameState` directly — no LLM needed; `assign_roles` itself doesn't call the LLM):
    - `test_default_unset_unchanged_under_seed` — `GRAPHIA_SEED=1234`, `GRAPHIA_ROLE` unset → assignment matches a frozen expected list (regression guard for §2.3 "Default behaviour unchanged when the variable is unset"; confirms the unset path is byte-identical to today's `assign_roles`).
    - `test_pin_mafia_seats_human_as_mafia` — `GRAPHIA_ROLE=mafia` → human's role is `"mafia"`; AIs contain exactly one `"mafia"` and five `"law_abiding"` (§2.1, §2.2).
    - `test_pin_law_abiding_seats_human_as_law_abiding` — `GRAPHIA_ROLE=law-abiding` → human is `"law_abiding"`; AIs contain two `"mafia"` + four `"law_abiding"` (§2.1, §2.2).
    - `test_role_value_is_case_insensitive` — `MAFIA`, `Mafia`, `mafia` produce the same assignment under the same seed (§2.1).
    - `test_invalid_role_value_exits_with_message` — `GRAPHIA_ROLE=villain` raises `SystemExit`; the message text names both `mafia` and `law-abiding` (§2.1 final criterion). **[Agent: testing]**
- [ ] **Sub 1.5:** `uv run pytest -q` — full suite green. **[Agent: testing]**
- [ ] **USER:** Manual smoke — three configs back-to-back (set in `.env` or inline as `GRAPHIA_ROLE=… uv run python -m graphia`):
    - (a) `GRAPHIA_ROLE=mafia` → role-reveal reads "Your role is Mafia.";
    - (b) `GRAPHIA_ROLE=Law-Abiding` → reads "Your role is Law-abiding Citizen.";
    - (c) `GRAPHIA_ROLE=villain` → game refuses to start, stderr names both valid choices, no Textual screen takeover.

## Slice 2 — `make play ROLE=` passthrough (§2.4 "Convenient launch via `make play`")

After this slice, `make play ROLE=mafia` and `make play-remote ROLE=law-abiding` seat the human accordingly without editing `.env`; bare `make play` falls back to whatever `.env` / shell provides.

- [ ] **Sub 2.1:** In `Makefile`, modify the `play` and `play-remote` recipes to prepend `$(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )` to the `uv run` invocation. When `ROLE` is unset, the prefix expands to empty and the command is unchanged; when set, it expands to `GRAPHIA_ROLE=<value> ` (trailing space) so the child process — and only the child — sees the env var. Validation of the value stays in Python (no Makefile-side check). **[Agent: python-backend]**
- [ ] **Sub 2.2:** `uv run pytest -q` — confirm no regression in the existing suite. **[Agent: testing]**
- [ ] **USER:** Manual smoke with `.env` containing no `GRAPHIA_ROLE`:
    - `make play ROLE=mafia` → "Your role is Mafia.";
    - `make play ROLE=law-abiding` → "Your role is Law-abiding Citizen.";
    - `make play` (no `ROLE` argument) → random assignment as before (§2.3 "Default behaviour unchanged when the variable is unset" still holds);
    - (Optional, if a Runtime is deployed) `make play-remote ROLE=mafia` → same pinned role against the hosted runtime.

## Slice 3 — Migrate magic-seed role-pinning tests to `GRAPHIA_ROLE` (§2.5 "Tests pin the human's role via `GRAPHIA_ROLE`, not via magic seeds")

After this slice, every test that today reaches for a `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constant *solely* to control which role the human is dealt has been migrated to set `GRAPHIA_ROLE` directly. `GRAPHIA_SEED` is retained only where a test asserts mechanical-RNG behaviour beyond role assignment (speech order, mafia-pointing target, vote tie-break). `tests/test_slice4_role_reveal.py` is the deliberate exception — its parametrisation IS the test (regression-guard for the unset-path seed-→role mapping per §2.3) — and its constants stay.

**Migration rule for every sub-task below (per ADR-006 "Test role-pinning convention: `GRAPHIA_ROLE` replaces magic-seed-for-role" and tech spec §2.6):**

1. Replace `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_*))` with `monkeypatch.setenv("GRAPHIA_ROLE", "<role>")` and **drop the seed setenv entirely by default**. `tests/conftest.py` unsets `GRAPHIA_SEED` for every test that does not opt in.
2. Before deleting the seed setenv, scan the surrounding test for assertions that depend on speech order, vote order, mafia-pointing target identity, or tie-break outcomes. If any are found, reintroduce an explicit `monkeypatch.setenv("GRAPHIA_SEED", "<value>")` next to the `GRAPHIA_ROLE` setenv with an inline comment naming the RNG behaviour being pinned, and rename the constant from `SEED_*` to describe what it actually pins (e.g., `SEED_NIGHT_POINTING_DETERMINISTIC`).
3. Defensive seed retention (a `monkeypatch.setenv("GRAPHIA_SEED", "0")` "just in case") is **forbidden**. Either the seed pins something the test asserts, or it goes.

- [ ] **Sub 3.1:** In `tests/test_dual_mode_smoke.py` (2 call sites at lines around 278 and 289), replace the two `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))` calls with `monkeypatch.setenv("GRAPHIA_ROLE", "law-abiding")` — drop the seed setenv entirely (conftest.py handles the unset default). If, on auditing the surrounding asserts, a specific seed value turns out to pin downstream mechanical-RNG behaviour (speech order, tie-break, mafia-pointing target), reintroduce `GRAPHIA_SEED` with an intent-naming comment and rename the constant per §2.6 of the tech spec — but do not preserve `GRAPHIA_SEED` defensively. Remove the `SEED_LAW_ABIDING` import if no longer used. **[Agent: testing]**
- [ ] **Sub 3.2:** In `tests/test_slice5_night.py`, migrate the two `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_LAW_ABIDING))` (line ~142) and `monkeypatch.setenv("GRAPHIA_SEED", str(SEED_MAFIA))` (line ~264) call sites. Note: line ~178 has a string `f"expected 2 AI mafia at seed {SEED_LAW_ABIDING}, …"` and line ~292 has `f"expected seed {SEED_MAFIA} to make the human Mafia; …"` — rephrase those error messages to mention the role instead. Delete the `SEED_MAFIA` and `SEED_LAW_ABIDING` module-level constants if no remaining references. **[Agent: testing]**
- [ ] **Sub 3.3:** In `tests/test_slice6_day.py` (3 call sites at lines around 141, 243, 385), migrate `SEED_LAW_ABIDING` setenvs. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.4:** In `tests/test_slice7_vote.py` (5 call sites at lines around 212, 395, 501, 635, 757), migrate `SEED_LAW_ABIDING` setenvs. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.5:** In `tests/test_slice8_endgame.py` (4 call sites at lines around 175, 335, 449, 585; constants `SEED_LAW_ABIDING` and `SEED_MAFIA_HUMAN`), migrate. Pay particular attention to the line-~335 site using `SEED_MAFIA_HUMAN` — that's the case where the test wants the human to be Mafia. Delete the constants. **[Agent: testing]**
- [ ] **Sub 3.6:** In `tests/test_slice9_polish.py` (2 call sites at lines around 132 and 288), migrate `SEED_LAW_ABIDING` setenvs. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.7:** In `tests/test_vote_validation.py` (7 call sites at lines around 182, 250, 309, 399, 564, 717, 804), migrate `SEED_LAW_ABIDING` setenvs. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.8:** In `tests/test_remote_mode_smoke.py` (2 call sites at lines around 385 and 469), migrate `SEED_LAW_ABIDING` setenvs. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.9:** In `tests/test_quit_modal.py` (1 call site at line ~128), migrate `SEED_LAW_ABIDING` setenv. Delete the constant. **[Agent: testing]**
- [ ] **Sub 3.10:** In `tests/test_vote_driver.py` (1 call site at line ~170, single inline `SEED` constant): review what's actually being pinned. If only role, migrate to `GRAPHIA_ROLE` and delete the constant; if the seed pins genuine mechanical behaviour, rename the constant per §2.6 of the tech spec. **[Agent: testing]**
- [ ] **Sub 3.11:** `uv run pytest -q` — full suite green. Treat any failure as a real signal that the seed in question was load-bearing beyond role-pinning; investigate per §2.6 of the tech spec rather than blanket-restoring the seed. **[Agent: testing]**
- [ ] **USER:** Skim the diff for the 10 migrated test files. Confirm (a) every kept `GRAPHIA_SEED` has a comment explaining why the specific value matters, (b) no `SEED_MAFIA` / `SEED_MAFIA_HUMAN` / `SEED_LAW_ABIDING` constants remain anywhere outside `tests/test_slice4_role_reveal.py`.
