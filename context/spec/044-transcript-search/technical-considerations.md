# Technical Specification: Searching Inside a Game Transcript

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md) (54 acceptance criteria across §2.1–§2.10 — 47 at authoring, +7 added during implementation (§2.6 and §2.8))
- **Status:** Completed
- **Author(s):** Alexey Tigarev

> **Every load-bearing claim below was measured against the installed packages** — Textual **8.2.4**, Rich **15.0.0**, Python 3.14 — not inferred from documentation. Numbers (wrap widths, row counts, contrast ratios, millisecond costs) came out of live `run_test()` sessions against the real 88 KB committed game `evals/transcripts/2026-09-05T17-58-24/game-01.txt` at 100×24, or from direct reads of the installed source. Where a measurement contradicted an assumption, the assumption is corrected here rather than quietly dropped.

---

## 1. High-Level Technical Approach

The change splits along the seam **spec 038 already established** and adds nothing structural:

| Layer | Where | What it owns |
| --- | --- | --- |
| **Pure** (no Textual, no I/O) | `src/graphia/eval_ledger.py` | Finding matches (smart-case), row arithmetic, and choosing the next/previous match. |
| **Widget** | `TranscriptScreen` in `src/graphia/ui/ledger_viewer.py` | The search bar, key routing, live re-highlighting, the scroll jump, and the status channel. |

This mirrors how spec 038 is built — a pure `tokenize_transcript` in the data layer, a thin component-class mapping in the viewer — and it is what lets most of the behaviour be tested without a terminal (§4).

**No new dependencies. No new module. No gameplay code is touched.** [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md) requires a default-on `GRAPHIA_*` ablation flag for gameplay-influencing changes and **exempts display-only ones**; this is display-only, so no flag.

**The hard problem is geometry, not search.** Transcripts are *few, very long lines* — the largest committed game is ~88 KB in ~405 lines, and the longest single line across the 420 committed games is 4,297 characters. The reading view wraps, so one source line can occupy forty rendered rows; that 88 KB game is **1,182 rendered rows** at 100 columns. "Put the match on the top line" (§2.5) cannot be served by counting newlines.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The pure matching layer — new functions in `src/graphia/eval_ledger.py`

Placed beside the existing pure matchers (`tokenize_transcript`, `row_matches_field`), Textual-free.

| Function | Contract |
| --- | --- |
| `search_is_case_sensitive(query) -> bool` | Smart case: true iff the query contains any uppercase character. |
| `find_matches(text, query) -> list[tuple[int, int]]` | Every match as a half-open `(start, end)` offset pair, in document order. Empty query ⇒ `[]`. |
| `wrap_row_starts(text, width) -> list[int]` | The start offset of every rendered row at wrap width `width`; its length **is** the rendered row count. |
| `row_for_offset(row_starts, offset) -> int` | `bisect_right - 1`. |
| `offset_for_row(row_starts, row) -> int` | The inverse. |
| `next_match(matches, offset, *, inclusive) -> tuple[int, bool] \| None` | `(index, wrapped)`; `None` when there are no matches. |
| `prev_match(matches, offset, *, inclusive) -> tuple[int, bool] \| None` | As above, backwards. |

Offsets in, offsets out: all row arithmetic lives in the pure layer, driven by a width the screen supplies.

**Matching must use `re.finditer(re.escape(query), text, re.IGNORECASE)` — never `text.lower().find(...)`.** `str.lower()` can *change length* (`'İ'` lowercases to two characters; measured 18 → 19 on a name-like string), which silently shifts every offset after it and would mis-highlight and mis-jump on exactly the kind of model-generated proper noun these transcripts are full of. `re.escape` also neutralises the `[` that `markup=False` already guards against. Matches are non-overlapping (`"aa"` in `"aaaa"` → offsets 0 and 2), which is what `less` does.

**Smart case (functional spec §2.7):** a query with no uppercase character matches case-insensitively; **any** uppercase character anywhere makes it exact. The spec does not state the `miRa` case, so it is pinned here and tested explicitly.

**Why not reuse `row_matches_field`.** The runs-list filter lowercases, splits on whitespace and **ANDs** the terms, because it answers a boolean *"does this row survive?"*. Transcript search needs **positions**, and a space must be literal so `I vote` finds the phrase rather than `"i" AND "vote"`. Same module, same purity, different question.

### 2.2 Offset → rendered row — mirroring Textual's own wrap pipeline

Read from the installed `textual/content.py` (`Content._wrap_and_format`), the pipeline is, per source line: `split(allow_blank=True)` → `expand_tabs(8)` → `divide_line(line.plain, width - line_pad * 2, fold=(overflow == "fold"))` → `divide(offsets)`, importing `from rich._wrap import divide_line` (`textual/content.py:18`).

`wrap_row_starts` reproduces this by **calling the same `divide_line`**, so agreement is by construction. Verified: row **count and content** match `Content.wrap` at widths 96, 80, 60, 40 and 200 on the 88 KB game — **zero mismatches**; and `len(body.visual.wrap(96)) == 1182 == scroller.virtual_size.height`.

Four traps, each handled rather than coded around:

- **`divide_line` returns *character* indices**, despite a docstring that says "cell offsets" — confirmed in the source. Accumulating per source line is therefore correct.
- **You cannot derive offsets by summing `Content.wrap(...)[i].plain` lengths.** Non-last divided rows are `rstrip()`ed, so the lengths do not add up. `divide_line` is the only correct source.
- **No committed transcript ends with a newline** (measured over all 420 in Slice 3), so the last rendered row of a real game is content, not a blank — which is what Task 3.6's row-content pin compares against. `wrap_row_starts` still emits the trailing empty row when one *is* present, because Textual renders it and dropping it would put the last row out of reach.
- **`expand_tabs(8)` runs before wrapping.** The corpus has **zero** tabs, so the mirror is exact today; a tab would shift offsets in expanded space. Pinned by a corpus test, not worked around.
- **The wrap width is not a constant — the biggest hazard.** At 100 columns, `body.content_size.width == 96` (100 − 2 scrollbar − 2 padding) while `region.width == 98`. A game that *fits* the viewport has no scrollbar and wraps at 98. **The screen must read the live `body.content_size.width`** and key its cached row map on it.

**`rich._wrap` is private.** Accepted deliberately: `textual/content.py` imports it itself, `rich>=15.0.0` is a direct dependency, and the agreement test (§4) makes a future break loud. Import it at module scope so a removal fails at collection time.

### 2.3 The highlight — background is the one axis left, and it costs a stated AA exception

Spec 038's `_kind_styles()` strips the background from every kind style, its docstring naming the reason: *"wrong the moment anything else (a future rule on `#transcript-body`, a selection highlight) sits underneath."* This feature is that case.

It is also the only axis left. The code's palette-exhaustion ledger records that all five theme foregrounds clearing WCAG AA on both builtin themes are spent, and all five usable `text-style` flags are spent — including `reverse`, which is moderator recaps and is precisely what `less` uses for matches. `overline` is measured dead.

**Hue is forced.** `$primary-muted`/`$secondary-muted` (blue) and `$error-muted` (red) are excluded on spec 038's own grounds — blue and red **mean side**, so a highlight in either would read as an allegiance claim. `$success-muted` measures worse (2.67 worst-case on dark). That leaves **amber** (`$accent`/`$warning`, identical and unspent), which is also the universal search-hit colour. `background: $accent N%` blends against the theme's own background, so it tracks light/dark automatically — and the percentage form gives **two steps from one hue** for other-vs-current, which `$accent-muted` cannot.

**Recommendation:** `transcript--match { background: $accent 20%; }` and `transcript--match-current { background: $accent 45%; }`.

**The honest cost, which the spec must state rather than bury: on `textual-dark` the highlighted state holds a 3:1 floor, not 4.5:1.** Measured at 20% (`#412f17`): `$text` 9.68, `$text-muted` 4.89, `$text-primary` 4.80, `$text-error` 4.33, `$text-secondary` **3.19**. The arithmetic is closed — `$text-secondary` already sits at 4.67:1 on dark with zero headroom, so *any* background light enough to see (≥1.1 contrast against the body) pushes it below AA. There is no value that satisfies both.

**The 45% current-match step is a second, steeper relaxation — measured in Slice 2, and NOT covered by the 3:1 floor above.** Against `#7c541d` on `textual-dark`: `$text` 5.06, `$text-muted` 2.56, `$text-primary` 2.51, `$text-error` 2.26, `$text-secondary` **1.67** — four of five below 3:1. The mitigating facts are narrower than for the 20% step but real: it applies to **exactly one span at a time**, the one the reviewer deliberately jumped to, sitting on the top row and named by the status count; and the light theme stays comfortable (worst case 4.33). Visibility against the body is 2.80 on dark and 1.23 on light, with 1.91 / 1.12 separating the two steps. **This is the sharper of the two costs and Task 5.5's by-eye pass now has two questions, not one:** whether the light-theme steps are distinguishable at all, and whether the dark current-match is legible. The lever is the rule's percentage and nothing else depends on either number.

This is defensible, and the reasoning belongs in the spec: the resting reading view keeps its AA guarantee untouched; the highlight is a transient, reviewer-initiated state one Esc away; `$text-secondary` is only the five cast-list field labels; and a match is redundantly located by the status count and by the jump putting it on the top row. The light theme is comfortable throughout (worst case 4.84). Light-theme separation between the two steps is weak (1.09) — **verify by eye during implementation; widen to 15%/50% if too subtle.**

**Two mechanism notes:**

- The highlight must **not** go through `_kind_styles`, which strips backgrounds by design. It needs its own resolver keeping only the background (`RichStyle(bgcolor=resolved.bgcolor)`). Foreground-free is what makes §2.3 true by construction. Use the **full** style, not `partial=True` — the same hazard `_kind_styles`' docstring already documents.
- **Layering is verified end to end.** `Text.stylize` appends, so highlight spans land after kind spans; `Content.from_rich_text` preserves and combines both. Measured at a match offset: `speech-mafia` → `rgb(209,126,146) on rgb(65,47,23)`; `field-label` keeps `bold`; `diary-substituted` keeps `italic underline strike`.
- **One measured consequence to write down:** a match inside a **moderator recap** combines to `reverse … on #412f17`, so the terminal swaps it — the *text* goes amber on a light ground rather than the ground going amber. Contrast ≈9.2:1, clearly legible and clearly marked, just inverted relative to every other kind. It is the only kind where this happens.

### 2.4 The search bar, focus, and key routing

**Three corrections to the obvious design, all measured.**

**(a) `AUTO_FOCUS` must be pinned, or a hidden `Input` steals initial focus.** `Screen._update_auto_focus()` filters on `Widget.focusable`, which checks `self.visible` — the `visibility` rule — **not `display`**. With `App.AUTO_FOCUS = "*"`, adding an `Input` to `compose()` makes it the screen's initial focus *even with `display: none`*, and every keystroke disappears into an invisible box. Measured. Fix: `TranscriptScreen.AUTO_FOCUS: ClassVar[str] = "#transcript-scroll"`, which also makes today's implicit behaviour explicit.

**(b) The six scroll keys belong on the `Input` subclass, not the Screen.** Screen-level bindings do *not* hijack the scroller (the focused-chain rule holds, measured both ways) — but they **cannot deliver `home`/`end` at all**, because `Input.BINDINGS` claims them and the focused widget wins: measured `home: 67 → 67`, `end: 67 → 67`, dead. Subclass `BINDINGS` *shadow* base-class ones, so putting all six on a `TranscriptSearchInput(Input)` works uniformly: measured `down 44→45`, `pagedown 45→66`, `up 66→65`, `pageup 65→44`, `home 44→0`, `end 0→1161`, with `value` unchanged throughout. This also removes the need for `action_home`/`action_end` overrides, removes the hijack question entirely, and means **no `priority=True` anywhere** (which would be actively wrong — it would break normal scrolling).

The bar stays a **sibling** of the scroller, but now purely as a layout decision rather than a routing one (inside the scroller it would scroll away).

Measured with the subclass in place, bar focused — §2.4 satisfied exactly, and no collision with today's exit keys:

| Key | Result |
| --- | --- |
| `up` / `down` / `pageup` / `pagedown` / `home` / `end` | scroll the game; query unchanged |
| `q`, `n`, `N`, `/` | typed into the query — no collision with `q` → leave-game |
| `backspace` | deletes a character — no collision with `backspace` → leave-game |
| `escape` | reaches the screen (§2.9) |
| `enter` | `Input.Submitted` → the jump (§2.5), free |

**(c) `action_close` branches on search *state*, not on whether the bar is open.** §2.9's last criterion requires that after Enter — bar hidden, highlights still showing — Esc clears the highlighting and **stays in the game**, with a further Esc leaving. Branching on "bar open" would pop the screen on that first Esc. Branch on a single `_search: SearchState | None` instead: if set, drop it, hide the bar, restore the subtitle, refocus the scroller, and **do not touch `scroll_y`**; otherwise `pop_screen()`.

A consequence worth stating rather than discovering: `backspace` and `q` share that `close` action, so they too clear a live search before leaving. §2.10 only promises unchanged behaviour to a reviewer who never presses `/`, so this is compliant — but it is a choice, not a side effect.

**What actually witnesses the hidden bar (measured in Slice 1).** `#search-row.size == (0, 0)` is **necessary but not sufficient**: an invisible widget leaves the screen's widget map, so `Widget.region` catches `NoWidget` and returns `NULL_REGION` under `visibility: hidden` just as under `display: none`. The assertion that carries §2.10 is the **scroller's height** — 22 with the bar closed, 21 with it open. Pinned by mutation: swapping the rule to `visibility: hidden` fails both the new test and §2.10's existing witness.

**Sizing.** A default `Input` is 3 rows; override to `height: 1; border: none; padding: 0 1;`. Measured: opening the bar moves `scroll.size.height` 22→21 and `max_scroll_y` 1160→1161, while `scroll_y` stays at 44 (§2.1 satisfied) and `body.content_size.width` stays 96 — **so opening the search does not rewrap and the row map stays valid.** The exception: a short game that fits 22 rows but not 21 gains a scrollbar and loses 2 columns, which is exactly why the row map is keyed on the live width.

### 2.5 The jump — and the end-of-game limit

`#transcript-scroll.scroll_to(y=row, animate=False, immediate=True)`. `immediate=True` matters: `Widget.scroll_to` otherwise defers through `call_after_refresh`, and pinning it removes a pause from every test.

**`scroll_y` clamps at `max_scroll_y`**, so a match in the last ~21 rows cannot sit on the top line. `less` behaves identically — neither tool can scroll past the end of the file.

**Resolved by the author (2026-09-12):** the criteria carry an explicit end-of-game caveat rather than padding the view with blank space to make the top line always reachable; padding was rejected because it would put emptiness that is not part of the game into a reading view §2.10 promises is unchanged. The implementation targets `min(target_row, max_scroll_y)`, and functional spec §2.5 AC1 and §2.6's criteria now read "…or, if that match lies within the last screenful, as close to the top as the game can scroll."

It also makes §2.6's current-match indicator **load-bearing rather than decorative**: inside that final screenful successive jumps produce no scrolling, so the indicator and the counter are the only feedback that anything happened.

**Resize:** highlights need no recomputation (spans are offset-based and width-independent); only the cached row map is invalidated. Measured at 100→70 columns: width 96→66, rows 1182→1593, `scroll_y` held at 149, and the match that was on row 149 moved to row 197. **Textual does not remap `scroll_y` on resize — the reading view already loses its place today**, so this spec inherits that rather than introducing it. Rebuilding the whole row map on the worst file costs **7.6 ms**, so a lazy rebuild when `content_size.width` differs is enough; no `Resize` handler is needed.

> **AMENDED by Task 5.2a (2026-09-14).** An **empty** Enter does not take this path at all — it routes through `_clear_search()` and ends the search. Nothing typed and nothing found look identical on screen (neither moves the game, neither lights anything), but they leave the reviewer holding different things: an empty query leaves a state they cannot see, so the next Esc is silently spent clearing it, against §2.9 AC3. A query that was *typed* and matched nothing is state (ii) and survives, because the count says so in as many words. **The test is `not query`, never `not query.strip()`** — `find_matches` is literal, so whitespace is typed text (a single space matches **14,524** times on the 88 KB game) and stripping would send the corpus's highest-yield query down the "you asked nothing" path. The clear is placed *before* `_row_starts`, so the 1,182-row wrap map is not built into a state that ceases to exist two lines later.

### 2.6 `n` and `N` step by row, not by match

**Resolved by the author (2026-09-12).** Several matches can fall on one rendered row, and since `n` scrolls by row, stepping match-by-match would make some presses do nothing visible. `n`/`N` therefore **skip past every match sharing the current row** and move to the next row that has one.

Two consequences the author accepted explicitly, both stated in the functional spec: the running count is an index among **all** matches, so it can jump (4 → 7); and the second and later matches on a row are highlighted but never become current.

**The selection rule** satisfying both §2.5 ("a match already on the top line is the one Enter lands on") and §2.6 ("`n` follows where you scrolled, not where you last jumped from"):

- **`n`/`N` are keyed on the row alone** — `step_match_by_row(matches, row_starts, scroll_offset.y, forward=…)`. It moves to the next row holding a match and reports the **first** match on that row, in both directions.
- **Enter** resolves inclusive-forward from the current top row via `next_match` directly. It must **not** be routed through the row-keyed step for tidiness — that is exactly what would break §2.5 AC2.
- **The step's origin is the current match's row when the reviewer has not scrolled since landing**, and `scroll_offset.y` when they have. `SearchState.scroll_at_landing` records where the view came to rest, so "have they scrolled?" is a recorded fact; the recorded value is re-clamped against the live `max_scroll_y` at comparison time, because hiding the bar after Enter re-fits the viewport and pulls a clamped landing back by one row.
- `current` is what the status line counts and what `transcript--match-current` paints — and, since Task 4.4a, the origin the step is taken from. It is still never stepped by `± 1`.

*(Task 4.4a, measured. Keying the step on `scroll_offset.y` alone dead-ends: the scroll clamps at `max_scroll_y`, so it cannot represent a position inside the final screenful, and any query with matches there — `the` has 702 on the 88 KB game, the last on row 1178 against a clamp of 1160 — returns the same match forever, with the wrap unreachable and the marker frozen. The cursor and the viewport must not be the same variable.)*

*(Corrected during Slice 4, before it was implemented. This rule previously read "if `current` is set and its row equals `scroll_offset.y`, step `current ± 1` with wrap; otherwise resolve against the row." That was self-contradictory: `current ± 1` can be a **row-mate**, which re-admits the press that does not move — the very thing the row-keyed resolution in the paragraph above exists to eliminate. Once the step is row-keyed, both branches collapse into one question and `current` drops out of it.)*

**Fixture blindness sank Slice 4's first round of probes, and the lesson is worth keeping.** Seven behaviours were probed and all passed while `n` dead-ended for any common word, because every probe used `vote` — whose 18 matches on the largest committed game all sit above `max_scroll_y` and **none of which share a rendered row**. A query that cannot reach the broken region cannot witness the bug, however thorough the check. Measured on that game at width 96: `vote` 18 matches / 0 crowded rows; `night` 38 / 3; `the` 702 / 154, last match on row 1178 against a clamp of 1160. **Tests touching the clamp, the wrap, or the row-mate skip must use `the` (or `night` for row-mates) and assert the fixture's premise before the behaviour.**

### 2.7 The status channel — `Screen.sub_title`

§2.6 and §2.8 together need a status that **outlives the prompt**: after Enter the bar is hidden, yet the reviewer must still see which match of the total they are on and be told when `n`/`N` wrapped. The cheapest correct channel, verified: `Screen.sub_title` — reactive, rendered live in the existing `Header`, **zero extra rows**. Measured `"…game transcript · Esc / Backspace to go back"` → `"… — vote — 3/18 (wrapped)"`. Restore `SUB_TITLE` when the search clears. (A second `Static` in the search row also works, but only while the row is shown.)

> **EXTENDED for Slice 6 (2026-09-15).** The status now also **names the keys that move between matches** (§2.8 AC5) — `the · 1/702 · n next · N prev` — because §2.6's original decision to keep `n`/`N` out of the Footer rested on this channel announcing them, and it did not. The hint is appended **last, after any `(wrapped)` suffix**, which is a width decision rather than a stylistic one: see §2.8a mechanism 3. Budget at a 100-column terminal is **60 columns**, not 78.

### 2.8 Live re-highlighting — cost, and the debounce that makes it viable

Measured per keystroke on the 88 KB / 1,182-row game (median of 8: `base.copy()` + `stylize` per match + content swap + pump to settle):

| query | matches | build | update | pump | total |
| --- | --- | --- | --- | --- | --- |
| `vote` | 18 | 1.4 ms | 3.8 ms | 72.7 | **77.9 ms** |
| `e` | 7,941 | 4.1 | 21.1 | 208.6 | **233.8 ms** |
| `" "` | 14,524 | 6.1 | 104.7 | 225.8 | **336.6 ms** |

On the corpus **median** file (33 KB, 564 rows) the same queries cost roughly 20–45 ms.

The cost is not in matching or styling — it is that `Widget._render_content` calls `Visual.to_strips(..., self.size.height, ...)` with the **full 1,182-row height**, not the 22-row viewport, so every repaint re-renders the whole widget (`to_strips` = 32.1 ms for `vote`, 113.3 ms for `e`). `refresh(region)` buys nothing because `_dirty_regions.clear()`.

**The shape that works:**

1. Build the styled base `Text` **once** and keep it.
2. Per update: `base.copy()` → `stylize(match_style, s, e)` per match → a **layout-free content swap** (a `Static` subclass that replaces the content and calls `refresh(layout=False)`; the plain text is provably unchanged, so the height cannot move — verified `virtual_size.height` stays 1,182). Worth ~15% (64.5 / 207.4 / 306.0 ms), and it guarantees the row map stays valid.
3. **Never `stylize` the base.** `Text.copy()` in Rich 15.0.0 does `copy_self._spans[:] = self._spans` — a fresh list — so the base is safe; mutating it would accumulate spans forever and old highlights would never clear.
4. **Coalesce `Input.Changed` through a short single-shot timer (~50–120 ms, restarted per keystroke).** This is what makes the first keystroke of any search — always the worst case, one character with thousands of hits — survivable. §2.2's "immediately" tolerates it.
5. **Do not window the highlighting to the viewport.** It saves only the `stylize` component (≤6 ms) and would force a re-style on every scroll step at 32–113 ms each; §2.4's "the newly revealed text is highlighted too" is free with whole-text spans and expensive with windowed ones.

The only structural fix would be a body widget with its own `render_line(y)`, since `_render_content` is not partial. Recorded, not built.

### 2.8a Discoverability — three Textual mechanisms that bite quietly (ADDED for Slice 6, 2026-09-15)

**Origin: the by-eye check in Task 5.5**, the one task in this spec no test could perform. The author searched the 88 KB game and could not reach the following match. Measured: repeated `/`+Enter is a no-op loop (four presses, all resting on row 7, header fixed at `the · 1/702`) — §2.5 AC2 working exactly as specified, since the jump is *inclusive* and after a jump the reviewer is already standing on a match. `n` had been correct throughout (rows 7 → 10 → 13 → 14). Nothing advertised it. **Slice 4 hid `n`/`N` on the explicit grounds that §2.7's status channel would announce them instead, and that announcement was never built** — a promise the code made about itself and did not keep. The lesson generalises past this spec: a "it will be announced elsewhere" justification is only as good as the elsewhere, and nothing mechanical was checking.

Three mechanisms, each of which fails *silently* and each of which cost measurement rather than reading:

**1. An empty description forces a binding hidden, `show=True` notwithstanding.** `textual/binding.py:161` computes `show=bool(binding.description and binding.show)`. `Binding("n", "next_match", show=False)` passed **no description at all**, so flipping `show` alone would have been a no-op with nothing anywhere to explain it. The same field is what `textual/widgets/_key_panel.py` renders, which is why the key help panel showed these two with a blank description column. **The description is load-bearing, not decoration.**

**2. `Footer.compose` groups by `binding.action` and renders only the first declared binding for that action.** So `Binding("enter", "next_match", ...)` can never add a Footer cell beside `n`: declared after `n` it is dropped; declared *before* `n` it **replaces** `n Next match` with `enter Next match`, silently undoing the discoverability fix that motivated the slice. `enter` is therefore deliberately `show=False` — a working synonym for a key that is named, rather than a named key that displaces the real one. (The same grouping is why `escape` + `backspace` have always rendered as a single `esc Back`.) Proved on a throwaway app and pinned by a mutation, not reasoned from source.

**3. `HeaderTitle` is 82 columns at a 100-column terminal, not 100.** `HeaderIcon` (`width: 8`, docked left) and `HeaderClockSpace` (`width: 10`, docked right) take 18 off the row. the source comment on `_SEARCH_STATUS_QUERY_MAX` claiming "74 of 100" was measuring the wrong widget (corrected in place); the `_SEARCH_STATUS_QUERY_MAX` cap of 32 was never wrong, only its believed headroom (**8 columns, not 26**). Since `HeaderTitle` is a one-row `Static` with `text-overflow: ellipsis` that truncates from the end, **the key hint goes last, after any `(wrapped)` suffix** — the ellipsis then spends the hint, and the position (a §2.8 acceptance criterion, readable nowhere else once the prompt has closed) survives. Measured worst case: `… · 702/702 (wrapped) · n ne…`.

**Enter needed no special case for the bar-open path, and got none.** `TranscriptSearchInput` is focused while the bar is open and `App._check_bindings` walks `focused.ancestors_with_self`, so the Input's `on_input_submitted` beats a plain screen binding — the same resolution order that already makes `n` type a literal `n` into an open bar (§2.4). Verified by keystroke rather than assumed, because a screen-level `enter` reaching the open bar would break §2.5's jump *and* Task 5.2a's empty-Enter clear together. Likewise no guard was added for the empty cases: `action_next_match` already returns on `_search is None` and on an empty match list, so Enter inherits §2.6 AC8 for free.

**Testing consequence.** Assert the Footer through the rendered `FooterKey` widgets (`key`, `description`), never through `BINDINGS` — the binding is the *input* to the thing under test, so asserting it is a tautology, and mechanism 1 above is visible only on the rendered side. Measured: deleting `TranscriptScreen`'s `Footer()` turns the rendered assertion red and the declaration assertion green, which is why both exist. This costs a private import of `FooterKey` from `textual.widgets._footer` (`textual.widgets` exports only `Footer`), taken with the same loud-canary reasoning as `rich._wrap.divide_line` in §2.2.

### 2.9 Component summary

- `AUTO_FOCUS = "#transcript-scroll"`; `BINDINGS += slash` (open search), `n`, `N`, and **`enter`** — the last three all no-ops when `_search is None`, and `enter` runs the *same action* as `n` rather than a second implementation of it.
- **Footer discoverability (resolved by the author 2026-09-12, REVISED by the author 2026-09-15 after Slice 6).** `slash` is bound `show=True` with the description `"Search"`, so the Footer reads `Search` beside the existing `Back` while reading a game. This is a deliberate, visible addition to the reading view: §2.10's promise is about the reading *area*, not the Footer, and the author chose discoverability over a pixel-identical Footer.
  - **`n` and `N` are now shown too**, with the descriptions `"Next match"` and `"Prev match"`. This reverses the original ruling that they "stay `show=False` — they are advertised nowhere, matching how `q` is already bound", which rested on §2.7's status channel announcing them instead. It never did. The by-eye check found the consequence directly: the author could not reach the following match. See §2.8a.
  - **`enter` is bound `show=False`** and cannot usefully be otherwise — `Footer.compose` groups by action and renders only the first declared binding, so a shown `enter` would either be dropped or *replace* `n`'s cell. It is a synonym for a key that is named, not a named key that displaces the real one.
- `COMPONENT_CLASSES |= {"transcript--match", "transcript--match-current"}`, two `background:`-only rules.
- `compose`: `Header()` → `Horizontal(id="search-row")` [`TranscriptSearchInput`, `Static(id="search-count")`] → `VerticalScroll` → `Footer()`.
- A `Static` subclass for `#transcript-body` exposing the layout-free content swap.
- `_search: SearchState | None` holding the query, matches, `current`, and the `(width, row_starts)` cache.
- Hiding: a CSS class toggle (`add_class("-open")`) over `widget.display`, so both states live in the stylesheet beside the kind rules.

---

## 3. Impact and Risk Analysis

### System Dependencies

- **`src/graphia/eval_ledger.py`** (4,078 lines) — gains the pure search layer; existing exports untouched.
- **`src/graphia/ui/ledger_viewer.py`** (2,138 lines) — `TranscriptScreen` only. `LedgerTableScreen`, `DetailScreen` and the runs-list filter are untouched.
- **New private-module dependency on `rich._wrap.divide_line`** — mitigated in §2.2.
- **No** change to the game engine, the eval harness, the ledger format, or any stored transcript.

### Potential Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| **A hidden `Input` steals initial focus** and swallows every key. | `AUTO_FOCUS = "#transcript-scroll"`; witnessed by the existing `assert screen.focused is scroller`. |
| **Wrap width read wrong** (96 vs 98) mis-jumps every real game. | Read live `body.content_size.width`; two fixtures at one terminal size, one overflowing and one not. |
| **`str.lower()` shifts offsets** on non-ASCII names. | `re.finditer(re.escape(q), text, re.IGNORECASE)`; pinned by a unit test on a length-changing character. |
| **Span accumulation** leaves stale highlights forever. | Always `base.copy()`; assert the base is never mutated. |
| **First keystroke stalls the UI** (336 ms worst case). | Debounced `Input.Changed`; layout-free swap. |
| **`rich._wrap` is private.** | Module-scope import (fails at collection) + hand-written-offset unit test + three-legged render pin. |
| **A back-out key typed into the box leaves the game** — `q`/`backspace` are today's exit keys. | Measured safe; pinned by a `(key, search_open)` parametrized sweep — the single most likely bug here. |
| **Search spans leak into a freshly-opened game** (§2.9 AC4). | Two existing strict-equality span tests already fail if this happens (§4). |
| **`input.value = ""` fires `Input.Changed`** and re-enters the highlight handler on close. | Guard the handler on `_search is not None`, or drop the state before clearing. |
| **Highlighted `$text-secondary` sits at 3.19:1 on dark.** | Stated openly in §2.3 with its rationale; resting view keeps AA; re-verify by eye and widen the steps if needed. |

### Concurrency note

Spec **043 (Keeping What a Stopped Measurement Run Produced)** is in active implementation on `main` and touches `src/graphia/tools/blunder_eval.py` and the ledger-write path. It does **not** touch `eval_ledger.py`'s transcript functions, `ledger_viewer.py`, or the viewer's tests, so the two proceed without collision.

---

## 4. Testing Strategy

Driven by a measured cost ratio: `tests/test_ledger_viewer.py` runs **90 items in 57.1 s** (~0.63 s per `run_test()` item); the terminal-free `tests/test_transcript_highlight.py` runs **305 items in ~2 s**. ~100× per item, and it decides placement.

**The split: 20 of the (then) 47 acceptance criteria are fully provable in the pure layer; 26 need a terminal.** *(The 7 added later — §2.6 AC9–14 and §2.8 AC5 — are **all** terminal-bound: they are claims about what the Footer, the key help panel and the header actually render, and about which widget a keypress reaches. They do not move the ratio's argument, they extend its terminal half.)* The rule: a criterion belongs in the widget layer only if removing Textual would make it *unstateable*. "Which text matches" and "which match is next" are not such criteria; "does `n` reach the screen rather than get typed into the box" is. The row-keyed navigation of §2.6 moves work *toward* the pure layer — "`n` skips matches sharing the current row" is a statement about `next_match`; only the resulting scroll is a widget fact.

### New file: `tests/test_transcript_search.py` (terminal-free)

Mirrors `test_transcript_highlight.py`'s role. Covers smart case (including the unstated `miRa` boundary and the length-changing-lowercase trap), what matches and how it narrows/widens, the next/previous truth table including both wraps and the same-row skip, and the counting — all parametrized.

**Corpus sweep, properties only, never golden counts** (the corpus grew 298 → 420 since spec 038; per-file expected counts rot within a week). One item per run dir with a coverage counter, following the existing `_discover_transcripts` idiom:

1. Every reported match round-trips: `text[start:end].lower() == query`.
2. The row starts **partition** `[0, len(text))` contiguously — no gaps, no overlaps. **Do not assert this by concatenating `text[starts[i]:starts[i+1]]`** (corrected in Slice 3): that rebuilds the text for *any* increasing sequence, because each row ends where the next begins by construction — it reads as the strongest check in the file and verifies nothing. Contiguity is carried by `starts[0] == 0`, strictly-increasing, and `starts[-1] < len(text)`; what pins the breaks in the *right places* is that **every `\n` is the last character of a row**, since Textual splits on newlines before wrapping.
3. Every match resolves to a row containing it, or straddles a fold into its successor.
4. Preconditions: zero tabs, zero of the five stripped control codes.

Plus a **non-vacuity** assertion that at width 96 the corpus really does produce a wrap-straddling match — otherwise property 3 passes by never firing. **The query matters, and `vote` will not do it** (measured in Slice 3): `divide_line` cuts at word *starts*, so a word short enough not to be folded always moves to the next row whole, and `vote` straddles **zero** times across all 420 games. A match can only span a fold if it contains the whitespace the cut fell on, so the sweep carries `" the "` — **2,637 straddling matches across 407 files**. An earlier draft of this section named `vote` here, which would have made the straddle half of property 3 dead code reading as coverage.

### New section `B15` in `tests/test_ledger_viewer.py`

Following B14's banner convention: **B15.1** opening/closing, focus, and the Esc state machine; **B15.2** live highlight as spans; **B15.3** scrolling while typing, parametrized over all six keys; **B15.4** jumping and the clamp; **B15.5** the subtitle status; **B15.6** the offset→row pin against the live render.

> **AS BUILT (2026-09-15):** two further sub-sections were added during implementation, both from defects the plan could not have predicted. **B15.7** — the three-state back-out machine, parametrized `(key, state)` over `escape`/`backspace`/`q` × bar-open / search-live / no-search, plus the empty / whitespace / no-match Enter trio from Task 5.2a. **B15.8** — Slice 6: the Enter walk compared against an `n` walk recorded in the *same* session (so the two cannot drift), Enter inside the open prompt still jumping per §2.5, and the Footer and status asserted through what is *rendered*. 166 items in that file; 3,183 in the suite.

**The three-legged pin** — what makes a Textual upgrade fail loudly instead of mis-jumping silently:

1. Row **count** against two independent Textual paths: `Content.get_height(body.styles, width)` and `body.size.height`. *(Not `scroller.virtual_size.height` — measured to be `max(content_height, viewport_height)`, so it reads 22 rather than 51 for a game that fits.)*
2. Row **content**: for every row, `body.render_line(row).text.strip() == text[start:end].strip()`.
3. The private-import canary of §2.2.

**Helper discipline.** Reuse `_open_the_only_game`, `_ledger_with_body`, `_transcript_body_content`, `_rendered_runs`, `_tokenizer_styled_runs`. Add a `_WRAPPING_GAME_BODY` fixture (`_LONG_GAME_BODY` is deliberately non-wrapping). **Do not reuse `_drive_search`** — it targets the runs-list filter *and* sets `Input.value` directly, which would make every key-routing test pass while testing nothing. Drive real keystrokes via `pilot.press(*query)`.

**A probe-subclass hazard to document:** `TranscriptScreen > .foo` selectors do **not** match from a subclass — scoped `DEFAULT_CSS` rewrites them, and a component class silently resolves to the bare body background. Real rules go in `TranscriptScreen.DEFAULT_CSS`; any test subclass needs `.foo` or `<Subclass> > .foo`.

**The §2.3 assertion writes itself** if search spans are appended after the kind spans: the leading slice of the rendered runs equals the tokenizer's runs exactly, and the tail is exactly the match spans. Pin that ordering explicitly — it is what makes "on top of, not instead of" checkable at all.

### Existing tests: two must change, one must not

> **AS BUILT (2026-09-15): fourteen changed, not two** — and the extra twelve are the rot-guards working rather than a plan error. Slice 6 tripped, in three groups: the `TranscriptScreen.BINDINGS` union guard (3 params, needing `enter`), the Footer shown-set guard (needing `n`/`N` with their descriptions), and **ten status-string assertions** that carried the full expected subtitle and so broke on one appended field they had nothing to say about. All were updated, none weakened to a subset or substring. The ten were centralised behind one `_expected_status(...)` helper with the hint spelled out as a **literal in the test module** rather than imported from production — an import would have made all sixteen sites agree with production *by construction*, so a rewrite to `n/N` (which names the keys but not which way each goes, answering half of §2.8 AC5) would have passed every one of them.

| Test | Action |
| --- | --- |
| `test_each_back_out_key_pops_the_transcript_screen` (`:4745`) | **Must change** — its rot-guard asserts `TranscriptScreen.BINDINGS` equals exactly the back-out keys. Split the constant and assert against the union, so a new binding still cannot be added undeclared. **And widen it**: parametrize `(key, search_open)` so each back-out key pressed *with a live search* is proven to edit the query (or clear the search) instead of leaving the game. |
| `test_no_key_path_reaches_a_bare_transcript_list_screen` (`:2718`) | **Must change** — add `slash`, `n`, `N` to `_DETAIL_KEYS_SWEPT`. Actively desirable: the existing fuzz-walk then presses them on the other two screens for free, discharging §2.10 AC4. |
| `test_scrolling_a_long_game_behaves_as_before` (`:4672`) | **Its assertions must stay unchanged.** §2.10's witness: `screen.focused is scroller`, `scroller.size.height == 22`, and `virtual_size.height` / `max_scroll_y` against the symbolic constants. Measured with `display: none` + `AUTO_FOCUS`, all still hold. **If any fails, fix the design, not the test.** *(Corrected during Slice 1: the fixture is **82** lines with `max_scroll_y` **60** — `_LONG_GAME_PROSE_LINES` is 80. The `202`/`180` figures this row first carried were copied from the committed test's own **stale docstring**, which is wrong on `main`; the assertions use the constants, so it has always passed. Task 5.6 corrects that docstring.)* |

Two further existing tests — `test_the_rendered_body_carries_a_span_per_styled_run_at_exact_offsets` and `test_a_real_committed_game_renders_as_its_exact_text` — assert **strict equality** of the full span list against the pure tokenizer's and never press `/`. They become the mechanical enforcement of §2.9 AC1/AC4 and §2.10 AC1: a leaked search span fails them. They must not be relaxed to subset assertions.

> **VERIFIED BY MUTATION in Task 5.3 (2026-09-14), rather than by reading.** The claim above is the kind this spec has been wrong about before, so it was tested rather than asserted. Both were green unmodified (3 passed — the second is parametrized `[pre-022]`/`[spec-022]`); both assert `runs == _tokenizer_styled_runs(...)` on the **whole list**, with no `set`, `>=`, `in`, slice or length-only form anywhere. **Leak mutation:** one `base.stylize("on #665c00", 0, 4)` in `TranscriptBody.__init__` — a body nobody searched carrying a match background — turned all three red. **Offset mutation:** shifting every span's `end` by one while leaving the *count* unchanged also turned all three red, so they are offset-exact and not merely count-exact. Neither test presses `/`: their only keys are `enter`, `t`, `down`×n, `enter` via `_open_the_only_game`.
>
> **The load-bearing piece is the helper, not the assertions.** `_rendered_runs` reads `content.spans` **unfiltered** — every span of every style lands in the compared list, which is the only reason a match background is visible to an equality against the tokenizer. A future refactor that narrowed it to particular style names would leave both tests **passing while enforcing nothing**. Protect the helper with the same care as the two assertions. (Contrast `_search_runs`, the search tests' helper, which deliberately slices the tokenizer prefix off — correct there, fatal here.)

### What is deliberately not tested

- **Exact highlight colours.** B14's convention is character ranges, not RGB. The checkable form of §2.3 is structural: the extra span sets a background and leaves `color` and `reverse` untouched, and the kind span beneath survives at its exact offsets. The one worthwhile colour assertion is that the highlight background differs from the body background.
- **Theme sweeps.** A background-only span makes no per-theme contrast claim; spec 038's three-theme sweep exists because *relationships between two styles* were the requirement there.
- **Snapshot tests.** `pytest-textual-snapshot` is a declared dev dependency but `snap_compare` appears in zero test files — not an adopted idiom, and a search bar is exactly what snapshots pin uselessly.
- **Rich's wrapping algorithm**, as opposed to our mirror's agreement with the render.
- **Any corpus sweep through `run_test()`** — 420 Textual sessions ≈ 4.5 minutes. Exactly one real committed game goes through a terminal, for the end-to-end round trip.
- **Performance timing.** No wall-clock assertions. The structural guards instead: the base `Text` is never mutated, span count equals match count, the tokenizer runs once rather than per keystroke, and one content swap per settle.
