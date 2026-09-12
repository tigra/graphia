# Technical Specification: Searching Inside a Game Transcript

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md) (47 acceptance criteria across §2.1–§2.10)
- **Status:** Draft
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
- **`expand_tabs(8)` runs before wrapping.** The corpus has **zero** tabs, so the mirror is exact today; a tab would shift offsets in expanded space. Pinned by a corpus test, not worked around.
- **The wrap width is not a constant — the biggest hazard.** At 100 columns, `body.content_size.width == 96` (100 − 2 scrollbar − 2 padding) while `region.width == 98`. A game that *fits* the viewport has no scrollbar and wraps at 98. **The screen must read the live `body.content_size.width`** and key its cached row map on it.

**`rich._wrap` is private.** Accepted deliberately: `textual/content.py` imports it itself, `rich>=15.0.0` is a direct dependency, and the agreement test (§4) makes a future break loud. Import it at module scope so a removal fails at collection time.

### 2.3 The highlight — background is the one axis left, and it costs a stated AA exception

Spec 038's `_kind_styles()` strips the background from every kind style, its docstring naming the reason: *"wrong the moment anything else (a future rule on `#transcript-body`, a selection highlight) sits underneath."* This feature is that case.

It is also the only axis left. The code's palette-exhaustion ledger records that all five theme foregrounds clearing WCAG AA on both builtin themes are spent, and all five usable `text-style` flags are spent — including `reverse`, which is moderator recaps and is precisely what `less` uses for matches. `overline` is measured dead.

**Hue is forced.** `$primary-muted`/`$secondary-muted` (blue) and `$error-muted` (red) are excluded on spec 038's own grounds — blue and red **mean side**, so a highlight in either would read as an allegiance claim. `$success-muted` measures worse (2.67 worst-case on dark). That leaves **amber** (`$accent`/`$warning`, identical and unspent), which is also the universal search-hit colour. `background: $accent N%` blends against the theme's own background, so it tracks light/dark automatically — and the percentage form gives **two steps from one hue** for other-vs-current, which `$accent-muted` cannot.

**Recommendation:** `transcript--match { background: $accent 20%; }` and `transcript--match-current { background: $accent 45%; }`.

**The honest cost, which the spec must state rather than bury: on `textual-dark` the highlighted state holds a 3:1 floor, not 4.5:1.** Measured at 20% (`#412f17`): `$text` 9.68, `$text-muted` 4.89, `$text-primary` 4.80, `$text-error` 4.33, `$text-secondary` **3.19**. The arithmetic is closed — `$text-secondary` already sits at 4.67:1 on dark with zero headroom, so *any* background light enough to see (≥1.1 contrast against the body) pushes it below AA. There is no value that satisfies both.

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

### 2.6 `n` and `N` step by row, not by match

**Resolved by the author (2026-09-12).** Several matches can fall on one rendered row, and since `n` scrolls by row, stepping match-by-match would make some presses do nothing visible. `n`/`N` therefore **skip past every match sharing the current row** and move to the next row that has one.

Two consequences the author accepted explicitly, both stated in the functional spec: the running count is an index among **all** matches, so it can jump (4 → 7); and the second and later matches on a row are highlighted but never become current.

**The selection rule** satisfying both §2.5 ("a match already on the top line is the one Enter lands on") and §2.6 ("`n` follows where you scrolled, not where you last jumped from") uses a `current: int | None`:

- If `current` is set **and** its row equals `scroll_offset.y`, `n`/`N` step `current ± 1` with wrap.
- Otherwise they resolve against `offset_for_row(scroll_offset.y)` — `n` inclusive-forward, `N` exclusive-backward.
- **Enter** always resolves inclusive-forward from the current top row.

### 2.7 The status channel — `Screen.sub_title`

§2.6 and §2.8 together need a status that **outlives the prompt**: after Enter the bar is hidden, yet the reviewer must still see which match of the total they are on and be told when `n`/`N` wrapped. The cheapest correct channel, verified: `Screen.sub_title` — reactive, rendered live in the existing `Header`, **zero extra rows**. Measured `"…game transcript · Esc / Backspace to go back"` → `"… — vote — 3/18 (wrapped)"`. Restore `SUB_TITLE` when the search clears. (A second `Static` in the search row also works, but only while the row is shown.)

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

### 2.9 Component summary

- `AUTO_FOCUS = "#transcript-scroll"`; `BINDINGS += slash` (open search), `n`, `N` (no-ops when `_search is None`).
- **Footer discoverability (resolved by the author, 2026-09-12):** `slash` is bound `show=True` with the description `"Search"`, so the Footer reads `Search` beside the existing `Back` while reading a game. `n`/`N` stay `show=False` — they are advertised nowhere, matching how `q` is already bound. This is a deliberate, visible addition to the reading view: §2.10's promise is about the reading *area*, not the Footer, and the author chose discoverability over a pixel-identical Footer.
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

**The split: 20 of the 47 acceptance criteria are fully provable in the pure layer; 26 need a terminal.** The rule: a criterion belongs in the widget layer only if removing Textual would make it *unstateable*. "Which text matches" and "which match is next" are not such criteria; "does `n` reach the screen rather than get typed into the box" is. The row-keyed navigation of §2.6 moves work *toward* the pure layer — "`n` skips matches sharing the current row" is a statement about `next_match`; only the resulting scroll is a widget fact.

### New file: `tests/test_transcript_search.py` (terminal-free)

Mirrors `test_transcript_highlight.py`'s role. Covers smart case (including the unstated `miRa` boundary and the length-changing-lowercase trap), what matches and how it narrows/widens, the next/previous truth table including both wraps and the same-row skip, and the counting — all parametrized.

**Corpus sweep, properties only, never golden counts** (the corpus grew 298 → 420 since spec 038; per-file expected counts rot within a week). One item per run dir with a coverage counter, following the existing `_discover_transcripts` idiom:

1. Every reported match round-trips: `text[start:end].lower() == query`.
2. The row starts **partition** `[0, len(text))` contiguously — no gaps, no overlaps.
3. Every match resolves to a row containing it, or straddles a fold into its successor.
4. Preconditions: zero tabs, zero of the five stripped control codes.

Plus a **non-vacuity** assertion that at width 96 the corpus really does produce a wrap-straddling match — otherwise property 3 passes by never firing.

### New section `B15` in `tests/test_ledger_viewer.py`

Following B14's banner convention: **B15.1** opening/closing, focus, and the Esc state machine; **B15.2** live highlight as spans; **B15.3** scrolling while typing, parametrized over all six keys; **B15.4** jumping and the clamp; **B15.5** the subtitle status; **B15.6** the offset→row pin against the live render.

**The three-legged pin** — what makes a Textual upgrade fail loudly instead of mis-jumping silently:

1. Row **count** against two independent Textual paths: `Content.get_height(body.styles, width)` and `body.size.height`. *(Not `scroller.virtual_size.height` — measured to be `max(content_height, viewport_height)`, so it reads 22 rather than 51 for a game that fits.)*
2. Row **content**: for every row, `body.render_line(row).text.strip() == text[start:end].strip()`.
3. The private-import canary of §2.2.

**Helper discipline.** Reuse `_open_the_only_game`, `_ledger_with_body`, `_transcript_body_content`, `_rendered_runs`, `_tokenizer_styled_runs`. Add a `_WRAPPING_GAME_BODY` fixture (`_LONG_GAME_BODY` is deliberately non-wrapping). **Do not reuse `_drive_search`** — it targets the runs-list filter *and* sets `Input.value` directly, which would make every key-routing test pass while testing nothing. Drive real keystrokes via `pilot.press(*query)`.

**A probe-subclass hazard to document:** `TranscriptScreen > .foo` selectors do **not** match from a subclass — scoped `DEFAULT_CSS` rewrites them, and a component class silently resolves to the bare body background. Real rules go in `TranscriptScreen.DEFAULT_CSS`; any test subclass needs `.foo` or `<Subclass> > .foo`.

**The §2.3 assertion writes itself** if search spans are appended after the kind spans: the leading slice of the rendered runs equals the tokenizer's runs exactly, and the tail is exactly the match spans. Pin that ordering explicitly — it is what makes "on top of, not instead of" checkable at all.

### Existing tests: two must change, one must not

| Test | Action |
| --- | --- |
| `test_each_back_out_key_pops_the_transcript_screen` (`:4745`) | **Must change** — its rot-guard asserts `TranscriptScreen.BINDINGS` equals exactly the back-out keys. Split the constant and assert against the union, so a new binding still cannot be added undeclared. **And widen it**: parametrize `(key, search_open)` so each back-out key pressed *with a live search* is proven to edit the query (or clear the search) instead of leaving the game. |
| `test_no_key_path_reaches_a_bare_transcript_list_screen` (`:2718`) | **Must change** — add `slash`, `n`, `N` to `_DETAIL_KEYS_SWEPT`. Actively desirable: the existing fuzz-walk then presses them on the other two screens for free, discharging §2.10 AC4. |
| `test_scrolling_a_long_game_behaves_as_before` (`:4672`) | **Its assertions must stay unchanged.** §2.10's witness: `screen.focused is scroller`, `scroller.size.height == 22`, and `virtual_size.height` / `max_scroll_y` against the symbolic constants. Measured with `display: none` + `AUTO_FOCUS`, all still hold. **If any fails, fix the design, not the test.** *(Corrected during Slice 1: the fixture is **82** lines with `max_scroll_y` **60** — `_LONG_GAME_PROSE_LINES` is 80. The `202`/`180` figures this row first carried were copied from the committed test's own **stale docstring**, which is wrong on `main`; the assertions use the constants, so it has always passed. Task 5.6 corrects that docstring.)* |

Two further existing tests — `test_the_rendered_body_carries_a_span_per_styled_run_at_exact_offsets` and `test_a_real_committed_game_renders_as_its_exact_text` — assert **strict equality** of the full span list against the pure tokenizer's and never press `/`. They become the mechanical enforcement of §2.9 AC1/AC4 and §2.10 AC1: a leaked search span fails them. They must not be relaxed to subset assertions.

### What is deliberately not tested

- **Exact highlight colours.** B14's convention is character ranges, not RGB. The checkable form of §2.3 is structural: the extra span sets a background and leaves `color` and `reverse` untouched, and the kind span beneath survives at its exact offsets. The one worthwhile colour assertion is that the highlight background differs from the body background.
- **Theme sweeps.** A background-only span makes no per-theme contrast claim; spec 038's three-theme sweep exists because *relationships between two styles* were the requirement there.
- **Snapshot tests.** `pytest-textual-snapshot` is a declared dev dependency but `snap_compare` appears in zero test files — not an adopted idiom, and a search bar is exactly what snapshots pin uselessly.
- **Rich's wrapping algorithm**, as opposed to our mirror's agreement with the render.
- **Any corpus sweep through `run_test()`** — 420 Textual sessions ≈ 4.5 minutes. Exactly one real committed game goes through a terminal, for the end-to-end round trip.
- **Performance timing.** No wall-clock assertions. The structural guards instead: the base `Text` is never mutated, span count equals match count, the tokenizer runs once rather than per keystroke, and one content swap per settle.
