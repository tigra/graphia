# Tasks: Reliable Game Exit Controls

- **Functional Specification:** `context/spec/003-reliable-game-exit/functional-spec.md`
- **Technical Considerations:** `context/spec/003-reliable-game-exit/technical-considerations.md`
- **Status:** Draft

## Slice 1 — Esc opens a "Quit?" overlay (and Ctrl+C still bypasses it)

After this slice the user can press Esc anywhere in the game, sees a y/n prompt, picks y to exit or n/Esc to stay. Ctrl+C still kills immediately. q is unchanged (still the old broken behaviour) — that's Slice 2.

- [x] **Sub 1.1:** Create `src/graphia/ui/quit_modal.py` — `QuitModal(ModalScreen[bool])` with bindings `y`/`enter` → `action_confirm` (dismiss True), `n`/`escape` → `action_cancel` (dismiss False). Compose: `Vertical` with a `Label("Quit? (y/n)")` and a `Horizontal` of `Yes` / `No` buttons. `on_mount` focuses the `No` button. `on_button_pressed` routes to dismiss(True/False). Self-contained CSS string. **[Agent: textual-tui]**
- [x] **Sub 1.2:** In `src/graphia/ui/app.py`, add `Binding("escape", "request_quit", "Quit", show=True, priority=True)` to `BINDINGS`. Add `action_request_quit(self)` that guards `if isinstance(self.screen, ModalScreen): return` then `self.push_screen(QuitModal(), self._on_quit_decision)`. Add `_on_quit_decision(self, confirm: bool | None)` that calls `self.exit()` on True. **[Agent: textual-tui]**
- [x] **Sub 1.3:** Add `tests/test_quit_modal.py` covering: Esc from idle game opens QuitModal; pressing `n` dismisses; pressing `y` exits the app; pressing `Esc` on the open modal dismisses; pressing `ctrl+c` while QuitModal is visible exits immediately (bypasses the modal); QuitModal is NOT pushed when a FailureModal is already on screen. Use Textual's `App.run_test()` pilot. **[Agent: testing]**
- [x] **Sub 1.4:** `uv run pytest -q` — must stay green. **[Agent: testing]**
- [x] **USER:** Manual smoke — `uv run python -m graphia` in a real terminal. At any prompt, press `Esc`. Confirm a "Quit? (y/n)" overlay appears with No focused. Press `n` — overlay dismisses, game state unchanged. Press `Esc` again, then `y` — program exits cleanly back to shell. _(Confirmed by user after the cancel-pending-future + os._exit fallback fix.)_

## Slice 2 — Roll back `q` as a quit key (scope reversal)

_Reversal: the original Slice 2 made `q` open the quit prompt with `priority=True` to win over focused text inputs. In real play this prevents typing any word starting with "q" in day chat — unacceptable UX. This revised slice removes the `q` binding entirely and reverts the `GraphiaInput` workaround. Functional spec §2.1a now requires `q` to be a normal printable character._

- [x] **Sub 2.1:** In `src/graphia/ui/app.py` BINDINGS, **remove** the line `Binding("q", "request_quit", "Quit", show=True, priority=True)`. Keep only `escape` and `ctrl+c`. **[Agent: textual-tui]**
- [x] **Sub 2.2:** In `src/graphia/ui/widgets.py`, remove the `GraphiaInput` subclass (or its `check_consume_key` override that excludes `q`/`Q`) — it was added solely to make the q priority-binding fire over a focused input, and is no longer needed. In `src/graphia/ui/app.py`, swap the `GraphiaInput(...)` usage back to plain `Input(...)`. Update the import. **[Agent: textual-tui]**
- [x] **Sub 2.3:** In `tests/test_quit_modal.py`, remove the three q-specific tests (`test_q_opens_quit_modal`, `test_q_works_while_text_input_has_focus`, `test_q_then_y_exits`) AND add a single new test `test_q_does_not_open_quit_modal` asserting that pressing `q` does NOT open the modal — both when an input has focus (the keypress should be captured by the input as text) and when no input has focus (no-op). **[Agent: testing]**
- [x] **Sub 2.4:** `uv run pytest -q` — must stay green. Expected count: 114 passed, 1 skipped (116 − 3 removed + 1 new = 114). **[Agent: testing]**
- [x] **USER:** Manual smoke — start a game, advance to Day chat, type "queen" or "quick" into the input. Confirm each `q` is typed as a letter (no quit prompt opens). Type `/vote Alice` and submit normally. Press `Esc` separately to confirm the quit prompt still works. _(Confirmed by user.)_

## Slice 3 — Spectator-mode discoverability hint

After this slice, when the human dies, the spectator-mode message visibly tells them how to leave.

- [x] **Sub 3.1:** In `src/graphia/ui/app.py` `_check_spectator_transition`, after the existing "[bold yellow]You have been killed. Watching as a spectator.[/]" line, append a second `private.write(...)` call: `Text.from_markup("[dim](Press Esc or q to exit.)[/dim]")`. **[Agent: textual-tui]**
- [x] **Sub 3.2:** Extend an existing spectator test (e.g. `tests/test_slice9_polish.py`) or add a new case in `tests/test_quit_modal.py`: after simulating the human's death, the private log contains BOTH the "You have been killed" line and the "Press Esc or q to exit" hint. **[Agent: testing]**
- [x] **Sub 3.3:** `uv run pytest -q` — must stay green. **[Agent: testing]**
- [x] **USER:** Manual smoke — play a game until your human is killed (or executed). Confirm the private log shows both the killed-message and the new exit hint. Press `Esc`, then `y` — program exits. _(Confirmed by user.)_