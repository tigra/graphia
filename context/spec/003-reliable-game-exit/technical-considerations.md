# Technical Specification: Reliable Game Exit Controls

- **Functional Specification:** `context/spec/003-reliable-game-exit/functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Add a small `QuitModal` ModalScreen and re-wire the App-level key bindings so `Esc` / `q` push the modal (instead of triggering Textual's default behaviour), while `Ctrl+C` keeps its existing immediate-exit path. The modal returns `True`/`False` via the standard `dismiss` callback; the App's wired callback calls `self.exit()` on `True` and is a no-op on `False`. Because `_drive()` is an async LangGraph stream task running in the same event loop, AI players continue posting messages while the modal is visible — no special pause logic needed.

The implementation reuses two existing patterns:

- **`FailureModal`** (`src/graphia/ui/failure_modal.py`) as the structural template for `QuitModal` (ModalScreen subclass, Vertical layout, BINDINGS list, on_mount focus, two Button outcomes).
- **`ConfirmWidget`** (`src/graphia/ui/widgets.py`) for the `y` / `n` binding shape.

No graph, state, persistence, or driver changes. Scope is entirely inside `src/graphia/ui/`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 New file — `src/graphia/ui/quit_modal.py`

A `ModalScreen[bool]` subclass named `QuitModal` that resolves to `True` (user confirmed quit) or `False` (user cancelled).

| Concern | Detail |
|---|---|
| Class | `QuitModal(ModalScreen[bool])` |
| BINDINGS | `y` → `action_confirm`, `enter` → `action_confirm`; `n` → `action_cancel`, `escape` → `action_cancel` |
| compose() | A `Vertical` with a `Label("Quit? (y/n)")` and a `Horizontal` of two `Button`s (`"Yes"`, `"No"`). Styled via a module-level CSS string analogous to `FailureModal`. |
| on_mount() | Focuses the **"No"** button — safer default for accidental `Enter` presses. |
| on_button_pressed() | Routes button presses to `dismiss(True)` / `dismiss(False)` matching the `y` / `n` keybindings. |
| action_confirm | `self.dismiss(True)` |
| action_cancel | `self.dismiss(False)` |

Nothing in the modal reads or mutates app state; it is fully self-contained, mirroring `FailureModal`'s posture.

### 2.2 `src/graphia/ui/app.py` — binding + action changes

Three discrete edits:

1. **BINDINGS list** — replace the two existing bindings with three:

   | Key | Action | Priority | Show | Purpose |
   |---|---|---|---|---|
   | `q` | `request_quit` | True | True | Always reaches the App, even when an input widget has focus |
   | `escape` | `request_quit` | True | True | Same; also overrides Textual's default Esc behaviour (focus-pop / screen-pop) — the source of the current "Esc collapses the UI but program keeps running" bug |
   | `ctrl+c` | `abort` | True | False | Unchanged — immediate kill |

2. **New method — `action_request_quit(self) -> None`** — pushes the modal:

   ```
   self.push_screen(QuitModal(), self._on_quit_decision)
   ```

   Idempotent: if the modal is already on the screen stack, Textual returns to it rather than stacking another. (Verified at implementation time; if Textual stacks duplicates, guard with `isinstance(self.screen, QuitModal)` before pushing.)

3. **New callback — `_on_quit_decision(self, confirm: bool | None) -> None`** — on `True`, call `self.exit()`. On `False` / `None`, return without action.

4. **`on_key` handler** — the existing "any key after `_game_over` exits" path stays as a fallback for keys NOT covered by the new bindings (e.g., Space at the end-of-game screen). `Esc` / `q` after game-over now go through the modal, matching the spec.

### 2.3 Spectator-mode hint (small polish)

In `_check_spectator_transition`, append a one-line hint to the existing spectator message so the affordance is discoverable:

```
"[bold yellow]You have been killed. Watching as a spectator.[/]"
"[dim](Press Esc or q to exit.)[/dim]"
```

### 2.4 Files touched

| Path | Change |
|---|---|
| `src/graphia/ui/quit_modal.py` | **New** — `QuitModal` ModalScreen + CSS |
| `src/graphia/ui/app.py` | Modify BINDINGS, add `action_request_quit` + callback, update spectator message |
| `tests/test_quit_modal.py` | **New** — `App.run_test()` snapshot/behavior tests (see §4) |

---

## 3. Impact and Risk Analysis

**System Dependencies**

- Textual `ModalScreen` + `push_screen(screen, callback)` — already in use for `FailureModal`.
- The async LangGraph driver (`_drive` task) keeps running independently of the modal stack; no driver coordination needed.

**Potential Risks & Mitigations**

| Risk | Mitigation |
|---|---|
| `priority=True` on `escape` may interfere with focus-management shortcuts in nested widgets (e.g., the day-chat input or vote prompts losing some Textual-default Esc behaviour). | Audit each in-game widget for any Esc-dependent behaviour during implementation; the project has none documented today. Testing covers each phase. |
| Stacking modals — if a `FailureModal` is already on screen (remote-mode crash) and the user presses `Esc`, the QuitModal could push on top. | Textual's screen-stack routes the keypress to the topmost screen first. `FailureModal` already binds `escape` to dismiss itself, so it wins. Explicit guard in `action_request_quit`: `if isinstance(self.screen, ModalScreen): return` — no-op while any modal is on top. |
| Pressing `Enter` while "No" is focused-by-default cancels — user expecting "Enter = Yes" gets surprised. | Acceptance criterion §2.1 says "Enter if the affirmative option is focused". On_mount focuses "No" intentionally; pressing `y` (explicit) confirms. The `enter`-binding-on-modal routes through the focused button. |
| Spectator-mode message change is rendered through `RichLog.write` — a freshly-started game may not have the private log mounted at the moment the kill fires. | Existing `_check_spectator_transition` already queries `#private-log` and would fail similarly; not a new failure surface. |

No impact on graph topology, persistence, AgentCore Runtime / Memory / Gateway code paths, or remote-mode plumbing.

---

## 4. Testing Strategy

All tests use `pytest` + Textual's `App.run_test()` pilot, in line with `tests/conftest.py` conventions:

- **`tests/test_quit_modal.py`** (new):
  - Pressing `Esc` from the role-count prompt opens `QuitModal`; pressing `n` dismisses it; pressing `y` exits.
  - Pressing `q` while the day-chat input has focus opens the modal (verifies `priority=True` overrides input-widget capture); after dismissal, the input retains any text typed before the press.
  - Pressing `Esc` while in spectator state opens the modal (simulate spectator by directly setting `_spectator=True` and rendering the spectator message, OR by manipulating `_latest_state["players"][human_id].is_alive`).
  - Pressing `Ctrl+C` while the QuitModal is open exits immediately (verifies the priority-True abort path bypasses the modal).
- **Existing tests** — re-run the full suite to confirm no regressions in `test_slice9_polish.py` (which covers the spectator + Ctrl+C path from spec 001).

The all-mocked `safe_llm` fixture means none of these tests touch Bedrock; the modal flows are pure UI.