---
spec: 005-play-as-role
spec_title: Play-As-Role via Environment Variable
introduced_on: 2026-05-24
---

# Concepts introduced in this increment

## Determinism & testing posture

- **Determinism posture as policy** (`determinism-posture-as-policy`) — Mechanical-RNG decisions are accepted as non-replayable across runs; tests pin a specific behaviour only when that behaviour is the test's subject, using the cheapest mechanism that expresses intent at the call site.
- **Role pin in tests via `GRAPHIA_ROLE` setenv** (`role-pinning-via-env-var-in-tests`) — Tests that need the human seated on a specific side say `monkeypatch.setenv("GRAPHIA_ROLE", "mafia")` directly rather than tunnelling intent through a magic `GRAPHIA_SEED` value that incidentally produces the desired deal.
- **Targeted monkeypatch of a production helper for test determinism** (`monkeypatch-shuffle-helper-for-determinism`) — When a test needs a specific day-speech order or vote-call order, it monkeypatches the production helper (`graphia.nodes.day._shuffle_order`) with a deterministic stub, rather than seeding RNG to nudge the helper indirectly.
- **In-test `random.seed` for byte-equal cross-mode parity** (`in-test-random-seed-for-byte-equality`) — The cross-mode dual-mode test calls `random.seed(...)` once at the start of each mode run inside the test body, the only place in the codebase that talks about seeding the RNG.

## GRAPHIA_ROLE feature

- **Role pin as a launch-time developer appliance** (`graphia-role-appliance`) — `GRAPHIA_ROLE=mafia` / `=law-abiding` (case-insensitive) in `.env` or on the command line pins the human's role for a session; an invalid value refuses to launch.
- **Pop-then-shuffle for composition-preserving role pinning** (`pop-then-shuffle-role-deck`) — When a role is pinned, `assign_roles` pops one card matching the pinned role out of the fixed 7-card deck, shuffles the remaining six, and places the pinned card at position 0 (the human) — preserving the 2-Mafia / 5-Law-abiding composition by construction.

## Surface mechanics

- **Surface config errors before Textual takes the alternate screen** (`fail-fast-load-config-before-tui`) — `load_config()` is invoked once in `__main__.py` after `load_dotenv()` and arg-parsing but before `GraphiaApp().run()`, so invalid env-var values raise `SystemExit` on stderr in the user's terminal rather than being swallowed by the TUI's alternate-screen takeover.
- **Make's `$(if VAR,ENV=VAR )` env-prefix idiom** (`make-conditional-env-prefix`) — The `play` / `play-remote` recipes prepend `$(if $(ROLE),GRAPHIA_ROLE=$(ROLE) )` to the `uv run` invocation so `make play ROLE=mafia` scopes the env var to the child process only, with no Makefile-side validation.

## Refactoring

- **Retiring an env-var protocol: the cascade** (`env-var-retirement-cascade`) — Removing `GRAPHIA_SEED` and `config.seed` cascaded through `config.py`, `setup.py::assign_roles`, `day.py::_shuffle_order` (lost a parameter), three RNG sites in `night.py`, three direct `GraphiaConfig(...)` constructions in tests, four stub-helper signatures, one autouse fixture, and the active docs (README, CLAUDE.md, architecture, ADR-006).
