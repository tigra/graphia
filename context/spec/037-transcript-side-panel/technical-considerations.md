<!--
Technical considerations for spec 037 — Transcript List as a Side Panel.
HOW the intermediate list screen collapses into a pane of DetailScreen, and how
focus moves between the two panes.
-->

# Technical Specification: Transcript List as a Side Panel in the Eval-Results Viewer

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

> **Sibling in-flight spec.** **038 (Colour-Coded Transcript Reading View)** changes how a transcript *reads* once opened (`TranscriptScreen`'s body rendering). This spec changes how one is *reached* (`DetailScreen`'s layout and `TranscriptListScreen`'s removal). The two touch the same module, `src/graphia/ui/ledger_viewer.py`, but different classes — expect a merge on that file only, with no overlapping hunks.

> **Verified against the source, not assumed.** `VerticalScroll.can_focus` is `True` and `ListView.can_focus` is `True`, so both panes can hold focus and the shared up/down keys resolve by focus with no custom key routing. `DetailScreen` is constructed at exactly one site (`LedgerTableScreen`, one `push_screen` call), which is what makes the entries-resolution choice below cheap.

---

## 1. High-Level Technical Approach

`DetailScreen` today is `layout: vertical` — `Header`, one `VerticalScroll(#detail-scroll)` holding a `Static(#detail-body)`, `Footer` — and its `t` binding pushes a `TranscriptListScreen` on top. That intermediate screen is deleted; its `ListView` moves into `DetailScreen` as a second, fixed-width pane beside the scroller.

Four changes carry the whole spec:

1. **`DetailScreen` becomes a horizontal split.** A `Horizontal` container holds the existing `#detail-scroll` (flexible) and a new fixed-width transcript pane. `Header` and `Footer` stay outside it, so the framing is unchanged.
2. **Focus moves by binding, not by key routing.** `right` focuses the panel, `left` focuses the scroller. Because both widgets are focusable, `up`/`down` need **no** handling at all — Textual delivers them to whichever has focus, which is exactly the functional spec's "the same two keys serve both".
3. **`t` becomes `action_focus_panel`** instead of `action_open_transcripts`. No screen is pushed.
4. **Selection still pushes `TranscriptScreen`.** The panel's `on_list_view_selected` does what the deleted screen's did. Popping back lands on `DetailScreen`, whose `ListView` is the same live widget and therefore still holds its index — the "same game still highlighted" criterion needs no state saving, but must be **tested** rather than assumed.

Affected files: `src/graphia/ui/ledger_viewer.py` (the only production file), `tests/test_ledger_viewer.py` (12 existing references to the removed screen). **Unchanged:** `src/graphia/eval_ledger.py` and its entire pure layer — `list_transcripts`, `read_transcript`, `transcript_dir_for`, `render_detail` — plus `TranscriptScreen` itself, the table screen, and the ledger.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — `DetailScreen` layout and the panel

| element | id | width | notes |
| --- | --- | --- | --- |
| `Horizontal` wrapper | `#detail-split` | `1fr` | replaces the screen's direct vertical stack |
| existing scroller | `#detail-scroll` | `1fr` | unchanged widget and content |
| transcript pane | `#transcript-panel` | **fixed 22 columns** | `ListView` + empty-state `Static`, both inside |

- **Fixed width, deliberately.** The functional spec requires the details pane keep a constant width as the reviewer moves between runs that have transcripts and runs that do not. A fixed column count guarantees that absolutely, because the width is not derived from content. 22 columns fits the known label shape (`game-01` … `game-50`) with room for the border. On a narrow terminal the **details** pane shrinks; the panel does not grow.
- **The panel is always present**, including for a run with no transcripts. It then hides its `ListView` and shows the existing `_NO_TRANSCRIPTS_MESSAGE` `Static` — reusing that constant and the hide-list/show-message posture verbatim from the screen being deleted, so the empty state is not reimplemented.

### Component B — where the entries come from

- **`DetailScreen.__init__` takes `entries: list[TranscriptEntry]`** alongside `record`; the single `push_screen(DetailScreen(record))` call site in `LedgerTableScreen` resolves them via the pure `list_transcripts(record, self.app._path)` — exactly the call `action_open_transcripts` makes today, moved one level out.
- **Why not resolve inside the screen:** `DetailScreen`'s own docstring states the invariant that *"the screen does no path arithmetic of its own"*, and `TranscriptListScreen` already takes its `entries` pre-resolved. Passing them in preserves both. Resolving in `on_mount` would work but would make the screen reach for `self.app._path` and quietly break that stated contract.
- Compose can therefore build the `ListView` rows directly, with no populate-on-mount step.

### Component C — focus movement

| key | action | behaviour |
| --- | --- | --- |
| `right` | `focus_panel` | focus the `ListView`; **no-op if already focused** |
| `left` | `focus_details` | focus `#detail-scroll`; **no-op if already focused** |
| `t` | `focus_panel` | the kept shortcut — same destination, no screen pushed |
| `up` / `down` | *(none)* | delivered by Textual to the focused widget |

- **Non-wrapping is the default, not extra work.** Each action focuses a specific widget, so pressing `right` twice simply focuses the panel twice. No cycle logic exists to accidentally introduce. **Corrected during implementation:** `left`/`right` are **not** free keys — `ScrollableContainer.BINDINGS` binds them to horizontal scrolling and both panes inherit it, and Textual checks the focused widget before the screen. A plain screen binding works only by accident (`action_scroll_right` raises `SkipAction` while `allow_horizontal_scroll` is `False`), and focus silently stops moving the moment a pane can scroll sideways. Both bindings therefore carry **`priority=True`**, pinned by a test that forces a scrollbar. The functional spec's "nothing happens, the screen does not change" is satisfied by construction — and should be asserted, since a later refactor to a `focus_next()`-style cycle would silently break it.
- **Focus starts on the details.** `on_mount` focuses `#detail-scroll`, matching the functional spec. Note the deleted screen's `on_mount` focused its list; that behaviour must **not** be carried over.
- **A run with no transcripts still accepts focus** on the panel, where `up`/`down` do nothing because the `ListView` is hidden and empty. No guard needed; assert it does not raise. (Verified: `Widget.focusable` gates on `visible`, which reads `styles.visibility` and is untouched by `display = False`, so focus lands and the pane's accent border still lights. Its `focus_chain` **excludes** the hidden list, which is a second reason to focus a named widget rather than use `focus_next()`.)

### Component D — focus indication (CSS)

- Both panes carry a border at all times; only its **colour** changes with focus. Nothing reflows when focus moves, because no border is added or removed — which is what keeps the layout still.
- Use Textual's **`:focus-within`** pseudo-class on `#detail-scroll` and `#transcript-panel` — **corrected during implementation**: `#transcript-panel` is a `Vertical` whose `can_focus` is `False`, so `:focus` would match nothing and the panel would look permanently unfocused; focus lands on the `ListView` inside it, and `has_focus_within` walks up to the container. Colours are **`$accent` focused / `$panel` unfocused** — also corrected: `$text-muted` is precedent for `color:` only, and resolves to `auto 60%`, which the parser **rejects** for `border:`; a screen whose CSS fails to parse never mounts. `$panel` is this repo's actual border precedent (`ui/app.py` uses the same pair).
- The 22-column panel width is inclusive of its border, so the usable label area is ~20 columns.

### Component E — deleting `TranscriptListScreen`

- Remove the class, its CSS, its bindings, and `DetailScreen.action_open_transcripts`.
- Keep `_NO_TRANSCRIPTS_MESSAGE` (moves to serving the panel).
- Keep `TranscriptScreen` untouched. Its `action_close` pops to whatever pushed it, which is now `DetailScreen` instead of the list — correct with no change.
- The module's `SUB_TITLE` for `DetailScreen` currently reads *"run detail · Esc / Backspace to go back · t for transcripts"*. Update it: `t` no longer opens transcripts, it moves focus.

### What does NOT change

The pure data layer (`eval_ledger.py`) in its entirety; `TranscriptScreen`'s rendering and keys; `LedgerTableScreen`; the ledger file; and the reading experience for a transcript once opened (spec 038's territory).

---

## 3. Impact and Risk Analysis

- **System dependencies.** `ledger_viewer.py` only, plus its tests. The pure layer is consumed unchanged, so the model-level tests in `tests/test_ledger_model.py` are unaffected.

- **Risk: the 12 existing `TranscriptListScreen` tests.** Deleting the screen invalidates a substantial block of `tests/test_ledger_viewer.py` — this is the largest single piece of work in the spec and is easy to under-estimate as "delete a class". *Mitigation:* treat the rewrite as its own task, and port each existing assertion to its panel equivalent rather than dropping it. What those tests currently prove (labels listed, selection opens the right game, empty state shows the message, back-out works) are all still requirements; only the surface changed.

- **Risk: "same game still highlighted" is assumed free.** Textual keeps the `ListView` widget alive across a `push_screen`/`pop_screen` of `TranscriptScreen`, so its index should persist. *Mitigation:* this is exactly the kind of framework assumption that fails quietly — assert it with a test that opens game 3, goes back, and checks the highlight, rather than reasoning about widget lifetimes.

- **Risk: focus indication is theme-dependent.** A border colour that reads clearly on one terminal theme may be invisible on another. *Mitigation:* use theme variables rather than literal colours, and assert the *focused widget identity* in tests rather than its rendered colour — appearance is reviewed by eye, focus is asserted programmatically.

- **Risk: 22 columns is a guess about future labels.** A backlog item (*Titled, stats-labelled transcripts*) wants entries labelled by matchup rather than `game-NN`, which would not fit. *Mitigation:* out of scope here and explicitly excluded by the functional spec; when that item lands it owns the width question. Note the number in one place so it is a one-line change.

- **No new dependency, no cloud access, no cost.** Entirely local UI work, verifiable headlessly.

---

## 4. Testing Strategy

Textual's `App.run_test()` harness throughout — the project's established convention for this viewer (`tests/test_ledger_viewer.py`). No real model, no network, no ledger writes; drive a temporary ledger outside the repo where a test needs specific records.

- **Layout:** opening a run shows both panes at once with nothing pressed; the panel is present for a run **with** transcripts and for one **without**; the details pane's width is identical in both cases (the no-shift criterion, asserted as a measured width, not by eye).
- **Focus movement:** starts on the details; `right` focuses the panel; `left` returns; `right` while already on the panel and `left` while already on the details both leave focus unchanged and push no screen; `t` focuses the panel and pushes no screen.
- **Shared up/down:** with the panel focused, `down`/`up` move the highlight and do not scroll the details; with the details focused, they scroll it and do not move the highlight.
- **Opening and returning:** selecting pushes a `TranscriptScreen` for the *right* entry (index-parallel, as the deleted screen proved); going back lands on `DetailScreen` with **the same entry still highlighted**; moving down and opening again reaches the next game with no intermediate screen.
- **The intermediate screen is gone:** no key path reaches a screen whose only content is a transcript list; `TranscriptListScreen` no longer exists in the module (an import of it fails).
- **Empty run:** panel present, message shown, focus can enter it, `up`/`down` raise nothing.
- **Ported coverage:** every assertion the 12 removed-screen tests made is re-expressed against the panel.
- Full `uv run pytest -q` green.
