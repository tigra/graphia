# Technical Specification: Reading a Game's Outcome Without Opening It

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md) (29 acceptance criteria across §2.1–§2.6 — 25 at authoring, +4 from the §2.2/§2.3 amendments made during this technical design)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **Every load-bearing claim below was measured, not inferred** — against the installed Textual **8.2.4** / Rich **15.0.0** / Python 3.14, through live `run_test()` sessions, and against the real committed corpus (**583** preserved games in **41** run dirs, 24.5 MB, as of 2026-09-15). Contrast ratios are read back per cell with `Screen.get_style_at` and scored with the WCAG relative-luminance formula. Where a measurement contradicted an assumption of mine, the assumption is corrected here rather than quietly dropped — §2.2 carries three such corrections.

---

## 1. High-Level Technical Approach

Two layers, along the seam the viewer already has.

**A pure derivation** in `src/graphia/eval_ledger.py`: a new `GameSummary` value object and a `summarise_game(text) -> GameSummary | None` function that reads a preserved game's text and returns the three numbers plus the winner. `list_transcripts` fills one in per entry. No Textual, no Rich, plain ints and strings out — the module's standing invariant.

**A presentation change** in `src/graphia/ui/ledger_viewer.py`: `DetailScreen` builds each panel row out of **CSS-classed segments** rather than one pre-styled string, so Textual's own cascade can give the winning side's count a different treatment on the highlighted row than on the other forty-nine. A header line above the list and a border title on the panel.

Nothing else moves. No ledger field is added, no transcript is rewritten, no measured run is repeated, no dependency is introduced, and the transcript writer is untouched. The change is display-only, so it is exempt from ADR 011's default-on feature-flag requirement (which exempts "display-only / non-gameplay changes" explicitly).

**No ADR.** The one decision that looks architectural — deriving per-game facts by parsing the preserved corpus rather than recording them in the ledger — has no alternative to weigh: functional-spec §2.5 requires all 583 already-preserved games to carry their numbers with nothing re-run, and only the committed text can supply that. Recording the facts at write time would serve future runs only, and would still need this parser for the corpus. There is no trade-off here to record.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The pure derivation — new section in `src/graphia/eval_ledger.py`

A new `# ===` section banner **after** the spec-017 locate/list/read section (ends line 1118) and **before** the spec-038 tokenizer section (begins line 1120). It is a sibling of locate/list/read — it consumes a transcript's text — and it must not land inside the tokenizer's territory, which is display work.

`GameSummary`, a frozen slotted dataclass matching the house style of `TranscriptEntry` and `TableModel`:

| field | type | meaning |
|---|---|---|
| `days` | `int` | Days the game played |
| `law_abiding` | `int \| None` | Law-abiding Citizens alive at the end |
| `mafia` | `int \| None` | Mafiosos alive at the end |
| `winner` | `str \| None` | `"mafia"`, `"law_abiding"`, or `None` |

`summarise_game(text: str) -> GameSummary | None` — pure, no I/O, never raises. Two absence levels, and they are **different states**, per functional-spec §2.4 vs the unreadable case:

- **Returns `None`** when the text yields no Day markers at all — an unreadable, empty or truncated file. The row then shows its number alone (§2.4's answer for that case).
- **Returns a `GameSummary` with `days` set and the other three `None`** when the game played Days but records no ending. The row shows `2d  —`.

`TranscriptEntry` grows one field, defaulted so the existing single construction site stays valid:

```
@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    label: str
    path: Path
    summary: GameSummary | None = None
```

`list_transcripts` fills it: after the glob, `summarise_game(read_transcript(path))` per file. Its docstring contract must be updated — it has read no file content since spec 017 and now does. Its defensiveness composes for free: `read_transcript` already returns `""` on any `OSError`, and `summarise_game("")` returns `None`, so an unreadable file lands on the bare-number row without a new branch.

**Cost, measured.** Reading and deriving all 50 games of the largest run directory in the corpus (`2026-09-14T19-40-56-df63a2`, 3,361,868 bytes) takes **34 ms** — 3.2 ms of file reading and 31.4 ms of parsing, stable across three trials. It is paid once, at the single `push_screen(DetailScreen(...))` site, on the worst case that exists. So: no caching, no lazy fill, no background work, no invalidation to get wrong.

### 2.2 The derivation rules, and the era evidence for each

The corpus spans **three** transcript eras, not two. Spec 022 (*Structured Eval-Transcript Format*) introduced the tags; two June-2026 runs predate it entirely and a third is a middle form. Each rule below is therefore stated together with the measurement that shows it holds in **every** era.

| Fact | Rule | Measured |
|---|---|---|
| `days` | Count lines that are exactly `<day>` at line start | Present in **583/583** files; equals `max(N)` over `Day N begins.` in **583/583**, 0 mismatches |
| roster | The line beginning `Full roster: `; count the parenthesised role labels | **573/573** files that have an ending; roster size equals the run's recorded `num_citizens + num_mafia` in **568/568** games whose record carries a lineup |
| deaths | Lines matching `• Night N: Name (Role)` and `• Day N: Name (Role) executed` | Both writer shapes come from one function, `nodes/endgame.py::_format_kill_line` |
| survivors | roster counts − deaths, by side | Win rule holds in **573/573** — see below |
| `winner` | Substring test for the winner sentences | **39/39** run-level tallies reproduce the ledger exactly — see §4 |

Three details are load-bearing and easy to get wrong:

**The winner sentence must be matched unanchored.** The pre-022 era writes it as `  Moderator: The Mafia have won.` — indented, behind a speaker prefix — while every later era writes it flush-left. `<endgame>` is absent from exactly **20** files (the two pre-022 runs), and **10 of those 20 did finish**, carrying a winner, a `Full roster:` and an `Events this game:`. A line-anchored match would silently lose those ten games. *(This corrects the functional spec's implication that a missing `<endgame>` means a missing ending: absence of the tag tracks the era, not the outcome.)*

**The death bullets and the persona bullets both begin `• `.** The `Who they really were:` section emits `• Name (Role) — …` with no cycle prefix. The `Night N:` / `Day N:` prefix is the only discriminator, so it must be required, not optional — otherwise every surviving player is counted as dead and every game reads `0-0`.

**The in-game `<recap>` standings are not final standings and must not be used.** The last recap of a game that ends on a Night kill reports the pre-kill counts: game 1 of the newest run reads `3 Law-abiding Citizens and 2 Mafiosos remain` in its final recap and must render `2-2`. A cross-check of roster-arithmetic against the first recap of each Day over the whole corpus gave 24 disagreements in 1,278 — **all 24 my check's fault**, because the June era posted its recap at the *end* of the Day round, after that Day's execution. An era-blind recap cross-check is not a safe oracle and is explicitly excluded from §4.

**Writer coupling.** The sentences and headers live in `src/graphia/prompts.py` as `ENDGAME_WINNER_LAW` / `ENDGAME_WINNER_MAFIA` / `ENDGAME_WINNER_RUNAWAY` / `ENDGAME_WINNER_DRAW` / `ENDGAME_HEADER_ROSTER` / `ENDGAME_HEADER_KILLS`, and the two role labels in `src/graphia/tools/eval_transcript.py::_ROLE_LABELS`. The pure layer keeps **its own literals** with a comment naming the writer, and a test pins each one to the writer's constant (§4). This keeps `eval_ledger.py` independent of the game package while making a reworded winner line fail loudly rather than blank every row.

`ENDGAME_WINNER_RUNAWAY` and `ENDGAME_WINNER_DRAW` both map to `winner = None`. Neither appears anywhere in the corpus (**0** occurrences of each), so both paths are handled but unwitnessed by real data — synthetic cases only.

**Defensive posture**, mirroring `_dig` as the whole section does: a malformed roster line, an absent header, an unparsable bullet, or arithmetic that would go negative all resolve to `None` rather than raising or rendering a negative count. The negative case has never occurred — the win rule held in 573/573 — so it is a guard, not a handler.

### 2.3 The row — CSS-classed segments, not a pre-styled string

This is the part the measurements changed, so the reasoning is recorded in full.

**The problem.** Textual's `ListView` repaints the highlighted row: `ListItem.-highlight` sets `color: $block-cursor-foreground`, `background: $block-cursor-background` and `text-style: $block-cursor-text-style`, with a second, dimmer set for the blurred state. Functional-spec §2.2 makes colour the sole statement of the winner. Measured on the accent background, that statement disappears:

| Row state | textual-dark | textual-light |
|---|---|---|
| Plain row | LA 5.61:1, Mafia 5.71:1 | LA 8.97:1, Mafia 4.45:1 |
| Highlighted, focused | LA 1.73:1, **Mafia 1.53:1** | LA 1.41:1, **Mafia 1.03:1** |
| Highlighted, blurred | LA 4.65:1, Mafia 4.12:1 | LA 2.98:1, Mafia 4.09:1 |

Six candidate background overrides were measured; only `$panel` cleared 4.5:1 on both themes (4.62 dark / 4.54 light), and it is so close to the surface colour that the highlight stops being visible. **The blue background is kept** — the author's decision — and the winner is therefore carried by **weight** on the highlighted row and by **colour** everywhere else.

**Why the row's composition has to change to do that.** Spec 038's recipe bakes resolved Rich styles into a `Text` at compose time (`_kind_styles`: `get_component_rich_style(...)` then `without_color + RichStyle.from_color(...)`). A baked span cannot know whether its row is the highlighted one, so that recipe can only switch treatment by watching `ListView.index` and regenerating two rows per keypress. Building the row from **widgets carrying CSS classes** lets the cascade do it with no watcher:

```
ListItem(Horizontal(
    Label("01   3d  ", classes="g-pre"),      # number + day, 9 cols
    Label("2", classes="g-cell g-law|g-dim"),  # Law-abiding survivors
    Label("-", classes="g-sep g-dim"),
    Label("2", classes="g-cell g-mafia|g-dim"),  # Mafia survivors
))
```

Ordinary rows: the winner's cell takes `$text-error` or `$text-primary` **plus bold**, the loser's takes `$text-muted`, the prefix takes `$text`. Highlighted rows: every cell takes `$block-cursor-foreground`, the winner's keeps `bold`, and every other cell **clears** it.

Measured with that CSS in place, all four states:

| State | winner | non-winner | discriminated by |
|---|---|---|---|
| dark, plain | side colour, bold, 5.61:1 | 6.28:1 | colour + weight |
| light, plain | side colour, bold, 8.97:1 | 4.97:1 | colour + weight |
| dark, highlighted, focused | bold, 3.78:1 | 2.57:1 | weight |
| dark, highlighted, blurred | bold, 9.59:1 | 5.42:1 | weight |
| light, highlighted, focused | bold, 7.83:1 | 4.58:1 | weight |
| light, highlighted, blurred | bold, 7.73:1 | 4.22:1 | weight |

**Two traps found by measuring, both of which silently break the mechanism:**

1. **Textual's `.-highlight` rule sets `text-style: bold` on the whole row.** If the non-winner cells do not clear it explicitly (`text-style: not bold`, in both the focused and blurred selector forms), every cell is bold and weight stops discriminating anything. A first pass had exactly this bug and read as "working" because the winner *was* bold — so was everything else.
2. **`$text-muted` is `auto 60%`** — a blend against the cell's own background, not a colour. It therefore re-resolves on the accent background to a washed blue-white, and in the measured run it beat the highlight override on selector specificity, leaving the loser's digit at **2.57:1** on dark-focused. The highlight override must set an explicit colour on the dim cell, and §4 pins the outcome rather than the selector.

**Three corrections to earlier readings of mine, recorded so they are not repeated:**

- I first measured the light theme by setting `app.theme` *after* pushing the screen and reported `$text-primary` at 1.69:1 on a plain light row. That was an artifact: the baked recipe resolves once at compose time, so I was measuring dark-theme colours on a light background. Resolved through CSS, `$text-primary` is a **dark** blue on textual-light (`#002d4f`) and gives **8.97:1**.
- The same artifact is a real property of spec 038's baked recipe, not just of my probe: a theme change while a screen is open leaves its spans stale. The CSS-classed rows in this spec do **not** have that problem — they re-resolve. This is a second, unlooked-for reason to prefer them here.
- **`textual-ansi` is now established, and the pair survives it.** My earlier ansi numbers were the probe lying — converting ANSI/default colours to truecolour for scoring reports black-on-black — so they are struck and nothing below is a ratio. **Spec 038's route instead** (`context/spec/038-transcript-syntax-highlighting/tasks.md`, the Slice 2 and Slice 4 ratification notes): ask each resolved style a **categorical** question — concrete hue, or collapsed to the terminal default (`Color.is_default`)? — and then a **relational** one — is the winner's cell a different colour from the loser's, and is exactly one segment bold? Spec 038 quotes no AA figure for this theme for the same reason this spec cannot: the background is the terminal's own.

  Measured that way, through `Screen.get_style_at` on the composited buffer with the theme set **before** compose, on the same three-row fixture the truecolour sweep uses: `$text-primary` and `$text-error` resolve to **concrete** hues here (`#5656ab` / `#ab5656`), while `$text`, `$text-muted` and `$block-cursor-foreground` all collapse to the terminal default. So:

  - **Ordinary row — both marks intact.** The winning count carries a concrete side hue *and* is the only bold segment, on a row that is otherwise the terminal's own foreground.
  - **Marked row — weight carries it, exactly as designed.** Every segment is the terminal default, and the winner is the only bold one. The `text-style: not bold` clears are load-bearing here too, not only on the truecolour themes: removing the two declarations takes the focused marked row from **1 of 4** segments bold to **4 of 4**, so trap (i) is live on this theme and the fix holds against it.
  - **The cross-view promise holds.** The list's two hues are identical to the reading view's `_kind_styles` pair under this theme (`#ab5656` Mafia / `#5656ab` Law-abiding) — functional-spec §2.2's fourth criterion, on the third theme.

  **Two degradations, neither of which costs the winner.** The loser is no longer *dimmed*: `$text-muted` collapses to the same terminal default as the prefix and the separator, so the losing count reads as ordinary body text rather than as part of one figure — the same accepted degradation spec 038 recorded for `marker`. And the **blurred** mark adds **italic to the whole row** (`$block-cursor-blurred-text-style` is `i` here, `none` on both truecolour themes); it lands uniformly on all four segments, so bold still discriminates.

  **What "three-theme support" therefore means:** on all three builtin themes a reviewer can tell which side won, on an ordinary row and on the row they are sitting on. It does **not** mean three measured contrast tables — no AA figure is quotable under `textual-ansi`, because the background belongs to the terminal, which is exactly the boundary spec 038 drew. Nothing is routed to the backlog: the theme does not collapse the pair.

**Cost of the row shape, measured** — 50 rows, median of five `run_test()` mounts:

| Row shape | Labels | Median mount | vs today |
|---|---|---|---|
| One plain `Label` (today) | 50 | 155.7 ms | ×1.00 |
| One `Label`, baked Rich spans | 50 | 160.9 ms | ×1.03 |
| Four CSS-classed `Label`s | 200 | 304.3 ms | ×1.95 |

So the per-state styling costs about **+148 ms** on opening a 50-game run, on top of §2.1's 34 ms. Both are paid once per detail screen, and the baseline includes app startup, so the marginal figure is smaller than the ratio suggests. Accepted: a fifth of a second, once, to keep the winner legible on the row the reviewer is sitting on.

**One `Label` per `ListItem` is no longer true, and one thing depends on it.** `tests/test_ledger_viewer.py::_panel_labels` reads each row with `item.query_one(Label)`, which raises on more than one match. That helper must become segment-aware; the established pattern for asserting on styled sub-spans is already in that file (`_transcript_body_content` / `_rendered_runs`).

**What must not change:** the `ListView`'s children stay `ListItem`s and nothing else. `watch_index` and `highlighted_child` both `assert isinstance(..., ListItem)`, and `Selected.index` is a position in `_nodes`, which is what makes `DetailScreen`'s index-parallel `_entries` contract work. The `Horizontal` goes *inside* the `ListItem`, never beside it.

### 2.4 The header line and the panel title

The header cannot live inside the `ListView` — see the `_nodes` contract above; a non-`ListItem` child would shift every selection index by one and trip those asserts. Textual 8.2.4 has no sticky-row or header affordance on `ListView` either.

So: a `Static(id="transcript-panel-header")` yielded **before** the `ListView`, inside `#transcript-panel`. It is a sibling, the list scrolls independently beneath it, and it therefore satisfies §2.3's "stays at the top" by construction. `on_mount` hides it alongside the list in the no-transcripts state, which §2.6's last criterion requires.

Column grid, 12 columns, aligned between header and rows:

```
cols   0 1 | 2 3 | 4 5 6 | 7 8 | 9 |10 |11
header   # |     | d a y |     | L | - | M
row    0 1 |     |   3 d |     | 2 | - | 2
```

`#transcript-panel` gains `border_title = "Games"` — the first in this module, though the idiom is already used in `src/graphia/ui/app.py`. It matches the footer's existing "Games" key hint. Set in `on_mount`, not in CSS.

**Geometry is unchanged.** `width: 22` stays; the row needs 12 of the ~20 usable columns, and the details pane keeps its measured 78. The two tests that pin this (`test_details_pane_width_is_identical_with_and_without_transcripts`, `test_panel_width_is_fixed_not_derived_from_its_content`) must keep passing untouched — they are the witnesses for §2.1's last criterion.

The CSS comment at `ledger_viewer.py:280-288` documents the 22 in terms of "the `game-01` … `game-50` label shape" and anticipates that "a future matchup-titled label shape would own this number". It must be rewritten to describe the new row grid and to say why 22 still holds.

---

## 3. Impact and Risk Analysis

### System Dependencies

- `list_transcripts` has **one** call site (`ledger_viewer.py:4372`), immediately before `push_screen`. `TranscriptEntry` is constructed in **one** place and consumed in eight; `.label` is read once and `.path` once. `TranscriptScreen` reads only `.path` and is unaffected.
- `summarise_game` is new and has no existing callers, so the derivation can be built and tested to completion before anything in the UI changes.
- No change to `evals/blunder-ledger.yaml`, to any record, to the transcript writer, or to the game.

### Potential Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **The parse drifts when the writer's wording changes.** | The pinning test (§4) fails the moment a winner sentence or header constant is reworded. The corpus sweep fails if a new era stops matching. |
| **Bold stops discriminating** because Textual's `.-highlight` bolds the whole row (trap 1). | A test asserts that on the highlighted row **exactly one** of the two counts is bold — the assertion a first pass would have failed while looking correct. |
| **The loser's count becomes illegible on the highlighted row** because `$text-muted`'s blend wins on specificity (trap 2). | A test asserts both counts clear 3:1 on the highlighted row in both focus states. Pinning the outcome, not the selector, so the fix can be tuned without rewriting the test. |
| **`textual-ansi` collapses the winner mark**, since `$text`, `$text-muted` and `$block-cursor-foreground` all resolve to the terminal default there. | **Established by spec 038's route — it does not** (§2.3). The two side hues stay concrete (`#5656ab` / `#ab5656`), so an ordinary row keeps colour *and* weight; the marked row keeps weight, and the `not bold` clears were verified load-bearing on this theme by mutation. The two degradations §2.3 records cost the loser its dimming, never the winner its mark. No contrast figure is quotable on this theme and none is claimed. |
| **Seven existing assertions break** — `_panel_labels(...) == ["game-01", …]` at `tests/test_ledger_viewer.py:1370, 1411, 1810, 1892, 2651, 2671` plus the `len()` form at `:1954`, and the helper itself at `:1740`. | Enumerated here so the task list carries them explicitly rather than discovering them as failures. The `== []` assertions (`:1589, 1856, 1900, 2384, 2812`) are unaffected. |
| The `plain_text` test helper falls back to `Text.from_markup(...)`, so a row containing `[` would be mangled. | The row alphabet is digits, spaces, `d`, `-` and `—`. Safe, and the segment-aware helper replacing it does not go through markup. |
| Mount cost roughly doubles for a 50-game run (+148 ms). | Measured and accepted; paid once per detail screen. A test must not assert wall-clock — there are no timing assertions anywhere in this suite. |
| **Stale corpus counts.** A live backlog item: ~70 lines across the repo still quote 298 or 373 files; it is 583 in 41 dirs. Three of those lines are in files this spec touches (`eval_ledger.py:1160-1163`, `ledger_viewer.py:551`, `:2786`). | This spec adds **no** new corpus-count literal to code or tests — every count-shaped assertion stays relative, following `test_transcript_highlight.py`'s convention. The existing stale lines are left dated, as spec 040 decided; the counts quoted in *this* document are dated in its preamble. |

---

## 4. Testing Strategy

Placement follows the measured cost ratio recorded in spec 044's tech spec: `tests/test_ledger_viewer.py` runs ~0.63 s per `run_test()` item, the terminal-free modules ~0.007 s. So the mass of coverage goes in a pure module and only genuinely screen-shaped assertions go through a terminal.

**New terminal-free module `tests/test_game_summary.py`** (topic-named, following spec 038's `test_transcript_highlight.py` and spec 044's `test_transcript_search.py`):

- **Unit cases per rule**, synthetic text: each era's winner form (flush-left and `  Moderator: `-prefixed), a Law-abiding win, a Mafia win, the runaway and draw sentences (→ `winner = None`), a game with Days but no ending (→ `days` set, rest `None`), empty and unreadable text (→ `None`), a roster whose bullets include the `Who they really were:` section, and a malformed roster line.
- **The writer-pinning test**: each local literal equals its `graphia.prompts` constant, and the two role labels equal `eval_transcript._ROLE_LABELS`' values.
- **Four oracles over the committed corpus**, parametrised one item per run dir, behind the existing `_requires_corpus` skip gate, read-only:

  1. **The ledger as oracle.** Per-game winners summed per run reproduce that run's recorded `outcomes.law_abiding.wins`, `outcomes.mafia.wins`, `runaway + draw + no_winner`, and `games`. Measured today: **39 record↔directory pairs, 0 mismatches.**
  2. **The win rule.** A Law-abiding win implies 0 Mafiosos alive; a Mafia win implies mafia > 0 and mafia ≥ law-abiding; no count is negative. Measured: **573/573 games, 0 violations.**
  3. **The recorded lineup.** Roster size equals `settings.lineup.num_citizens + num_mafia`. Measured: **568/568, 0 mismatches** (the shortfall is records predating the lineup field, not failures).
  4. **Day-count agreement.** `count("<day>")` equals `max(N)` over `Day N begins.`. Measured: **583/583, 0 mismatches.**

- **Properties, never golden counts** — no assertion names 583, 573, 41 or 39; each is computed, per the corpus-staleness convention.
- **Explicitly excluded:** any cross-check against the in-game `<recap>` standings, for the era-dependent reason in §2.2.

**New section `B16` in `tests/test_ledger_viewer.py`** (screen coverage is appended as a lettered section, never a new file — sections run B1…B15.8 today):

- Row content: number without the `game-` prefix, day count, both survivor counts, in the §2.1 column grid.
- The six-row golden from §2.1's second criterion, against a fixture reproducing that run's shape.
- Winner marking: the winning side's cell carries its side colour on an ordinary row; the loser's is dimmed; **exactly one of the two is bold on the highlighted row**, in both focus states; both clear 3:1 there.
- The parity case — both counts `2` — is distinguishable.
- The `2d  —` row for a game with no ending, and the bare-number row for an unreadable one.
- The header: present, column-aligned with the rows, hidden together with the list in the no-transcripts state; the panel's border title reads "Games".
- Geometry: the panel is still 22 and the details pane still 78.
- Selection still resolves index-parallel after the row-shape change — the `ListItem`-only `_nodes` contract holds with the `Horizontal` inside.

Assertions read widget and model state through the pilot API and span structure through a segment-aware helper — never live-rendered bytes, and no snapshot testing (`snap_compare` appears in zero test files in this repo).
