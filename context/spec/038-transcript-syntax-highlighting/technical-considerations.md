<!--
Technical considerations for spec 038 — Colour-Coded Transcript Reading View.
HOW a pure tokenizer turns transcript text into styled spans, how the UI paints
them, and how the person's own seat becomes identifiable.
-->

# Technical Specification: Colour-Coded Transcript Reading View

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **Sibling spec.** **037 (Transcript List as a Side Panel)** is Completed and changed how a transcript is *reached*; this changes how one *reads*. 037 left `TranscriptScreen`'s rendering untouched precisely so this spec could own it. Both live in `src/graphia/ui/ledger_viewer.py` but in different classes.

> **Everything asserted below was checked against the installed source and the committed corpus, not inferred.** Four claims in spec 037's technical document turned out to be wrong about this same Textual version, so each load-bearing fact here carries how it was verified.

---

## 1. High-Level Technical Approach

`TranscriptScreen.compose` currently does one thing: `Static(read_transcript(path), id="transcript-body", markup=False)`. The `markup=False` is deliberate and documented — the transcript's literal `<transcript>` / `<day>` tags must not be parsed as console markup.

The change splits into three parts:

1. **A pure tokenizer** in `src/graphia/eval_ledger.py` turns transcript text into a sequence of `(text, kind)` spans, where `kind` names a *semantic* role (`marker`, `attr`, `speaker`, `speech`, `thought`, `recap`, `field-label`, …) and never a colour. No Rich import, no Textual import — the layer's stated property is preserved, and the parsing (the part most worth testing) becomes a pure-function test with no terminal.
2. **The UI maps kinds to styles** and builds the renderable. Theme choices stay where the viewer's other colours already live.
3. **The writer gains a human-seat marker** so a transcript states plainly which seat the person played, with the reader inferring it for the 298 already-committed transcripts that carry no marker.

Affected files: `src/graphia/eval_ledger.py` (the tokenizer), `src/graphia/ui/ledger_viewer.py` (`TranscriptScreen` only), `src/graphia/tools/eval_transcript.py` (the writer's human marker), plus tests. **Unchanged:** the ledger, `render_detail`, `list_transcripts`/`read_transcript`, `DetailScreen` and its panel, `LedgerTableScreen`, and every existing transcript file.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — the pure tokenizer (`eval_ledger.py`)

A function taking the transcript text and returning an ordered list of spans. Suggested shape — one entry per contiguous styled run, concatenating back to the input **exactly**:

| kind | matches | notes |
| --- | --- | --- |
| `marker` | `<transcript>`, `<setup>`, `<preamble>`, `<night>`, `<day>`, `<round>`, `<endgame>`, `<kill>`, closing forms, the top metadata line, `Round N.` | the skeleton; must read *quieter* than content |
| `attr` | the `name=` / `role=` / `player=` / `initiator=` / `target=` values inside a marker | so specifics are picked out of punctuation |
| `speaker` | the `Name:` prefix of a spoken line | side-coloured (below) |
| `speech` | the words after `Name:` | side-coloured |
| `thought` | `<thought player="X">…</thought>` content | must be unmistakably private |
| `recap` | `<recap>…</recap>` content | fact, not opinion |
| `field-label` | `Personality:`, `Manner:`, `Public legend:`, `True self (hidden):`, `Persona:` | cast-list skimming |
| `plain` | everything else | the fallback that makes degradation total |

- **Round-trip invariant:** `"".join(text for text, _ in spans) == original`. This is the single most valuable property to test — it makes "the stored game is never altered" (functional-spec §2) structurally true rather than merely intended, and it catches any tokenizer that drops or duplicates a character.
- **Sides come from a parsed cast list.** The tokenizer first reads `<setup>` for `name → role`, classifies each role as Mafia or Law-abiding, then colours later `speaker`/`speech` spans by that map. A name absent from the map yields `plain` speech — never a guess.
- **Kinds are semantic, never colours.** `speaker-mafia` is acceptable as a kind; `speaker-red` is not.

### Component B — the two transcript formats

The committed corpus has **two** formats. Verified by scanning all 298 files:

| | files | runs | cast list | notes |
| --- | --- | --- | --- | --- |
| spec-022+ | 268 | 12 | `<player name="…" role="…">` | `<vote>` in 261, `<thought>` in 216 — **absence is normal**, not corruption |
| pre-022 | 30 | 3 (2026-06-19, 2026-06-20 ×2) | indented `Name — Role` | `<recap>`/`<kill>`/`<endgame>` in only 10, `<vote>` in 3 |

- **Best-effort, per the author's decision.** The tokenizer parses the spec-022 cast list only. A pre-022 file still gets `marker`, `speaker`, `speech`, `field-label` and `plain` spans — it simply has no side map, so its speech is `plain` rather than side-coloured. **Nothing errors.**
- This is also the forward-compatibility posture: a future format change degrades the same way instead of raising.
- **Do not** add a second cast-list parser for the old form; that was considered and declined.

### Component C — the UI mapping (`ledger_viewer.py`)

- `TranscriptScreen.compose` passes a **styled renderable** instead of a plain string. Verified against the installed Textual: constructing `Static(rich.text.Text(...), markup=False)` works, and `Static.render()` returns Textual's own `Content` with the spans **preserved** (`[Span(0, 4, style=Style(bold=True))]`). Note Textual 8.x's native type is `Content`, so a future refactor may prefer building that directly; a Rich `Text` is verified working today.
- **Correction, measured during Slice 1: preferring `Content` is not purely stylistic.** `rich.text.Text.append` runs `strip_control_codes`, which **silently drops** BEL, BS, VT, FF and CR (`\x07 \x08 \x0b \x0c \x0d`) — so a transcript containing any of them would render one character short of its file, breaking the exact guarantee this view exists to keep. Verified on Textual 8.2.4: `\n` and `\t` survive, and **zero of the 298 committed transcripts** contain any of the five, so the round trip holds today. It is invisible to the tokenizer's own round-trip test — **only a widget-level assertion catches it**.
- **Correction to the correction, measured in Slice 1's test task: switching to `Content` does NOT fix this.** `textual/content.py` carries its own `_STRIP_CONTROL_CODES = [7, 8, 11, 12, 13]` — the identical five — and `Content.__init__` applies it by default, so `Content("a\x07b").plain == "ab"` exactly as the Rich `Text` path does. Closing the hole requires an explicit `strip_control_codes=False`, **whichever renderable is chosen**. The choice between `Text` and `Content` is therefore stylistic after all — but neither is lossless by default, and a tripwire test pins the boundary so the day a transcript carries one of the five is a test failure rather than a silently short render.
- **`markup=` only affects the `str` path.** `textual.visual.visualize` sends a `str` through `Content.from_markup(...) if markup else Content(...)`, but a Rich `Text` always goes via `Content.from_rich_text`. Passing a `Text` therefore already bypasses markup parsing; `markup=False` stays as the guarantee for the string path a future refactor might take, not as what protects the `[`.
- **`Static` exposes no public `markup` attribute** on Textual 8.2.4 — the constructor kwarg lands on `Widget` as `self._render_markup`. A test must assert `body._render_markup is False`; `body.markup` raises `AttributeError`.
- **`markup=False` stays.** Do not switch to a markup string. Today's corpus happens to contain no `[`, but persona prose is model-generated and could contain one at any time, and the existing comment names this hazard explicitly. Spans avoid markup parsing entirely.
- One style per kind, defined in the screen's CSS or a module-level mapping, using theme variables — following spec 037's correction that `$text-muted` is valid for `color:` but **not** for `border:` (here we only set text colour, so `$text-muted` is appropriate for the quiet `marker` kind).
- **Plain-text fallback:** where colour is unavailable the spans' text still concatenates to the original, so the view degrades to exactly today's output with no stray characters.

### Component D — identifying the person's seat

**Writer (`eval_transcript.py`), for new transcripts.** `render_transcript` already receives `players: dict[str, PlayerState]`, and `PlayerState.is_human` exists (`state.py`) — but `is_human` appears **nowhere** in the writer today. Add an attribute to the human's `<player>` tag (e.g. `human="true"`), sourced from that flag.

Worth knowing why the existing signal is not already this: `(no persona recorded)` is a **side effect**, not a marker — only AI players get personas, so the human's is `None`. It happens to be reliable (exactly one per transcript in all 298) but it encodes "no persona", not "the person".

**Reader, for the 298 existing transcripts.** When no marker is present, infer from the preamble's `Moderator: A new game begins. Welcome, <name>` line. Verified across all 298: present exactly once in every file, and where the cast list is parseable it never disagreed with the `(no persona recorded)` seat.

**Bold applies within the side colour**, so the seat is marked more strongly than its side-mates rather than differently coloured. A seat that cannot be identified (neither marker nor inferable name) simply is not bolded — never guessed.

### What does NOT change

Any transcript file on disk; the ledger; `read_transcript` / `list_transcripts` / `render_detail`; `DetailScreen` and its transcript panel; `TranscriptScreen`'s keys, scrolling and stack behaviour; the words, order and structure of a transcript's content.

---

## 3. Impact and Risk Analysis

- **System dependencies.** `eval_ledger.py` (new pure function), `ledger_viewer.py` (`TranscriptScreen` only), `eval_transcript.py` (writer marker). Existing transcripts are read-only inputs.

- **Risk: the tokenizer silently alters what the reviewer reads.** A dropped or duplicated character would be invisible against a wall of text. *Mitigation:* the round-trip invariant, asserted over **every committed transcript** in the corpus — 298 real files, including both formats and the odd shapes (`<vote>`-less games, `<thought>`-less pre-028 runs). That is a cheap, exhaustive property test and it should be written first.

- **Risk: re-enabling markup parsing.** Switching to a markup string would make a single `[` in model-generated persona prose either vanish or raise. *Mitigation:* spans only; keep `markup=False`; assert that a transcript containing `[bold]`-looking text renders it literally.

- **Risk: the side map mis-colours.** Colouring by side is the author's explicit choice (recorded in the functional spec) and it makes a wrong map actively misleading rather than merely ugly. *Mitigation:* a name absent from the cast list yields `plain`, never a guess; assert that a speaker not in `<setup>` is uncoloured.

- **Risk: the writer change looks like a format change.** Adding an attribute to `<player>` alters newly-written transcripts. *Mitigation:* it is purely additive to one tag; the existing writer tests should pin that nothing else about the emitted text moves, and the tokenizer must treat the attribute as optional (all 298 existing files lack it).

- **Risk: the reader's inference is a heuristic.** It depends on the moderator's greeting wording. *Mitigation:* it is a documented fallback behind an explicit marker, not the primary route — and the marker makes it progressively less load-bearing. Assert both paths, and assert that the marker wins when both are present.

- **No new dependency, no cloud access, no cost.** Rich and Textual are already dependencies; the tokenizer adds neither.

---

## 4. Testing Strategy

- **Round-trip over the whole corpus (write this first):** for every file under `evals/transcripts/`, the tokenizer's spans concatenate to the file's exact text. Read-only; no fixture authoring; covers both formats and every element-presence combination the corpus actually contains.
- **Tokenizer unit tests** on small synthetic inputs, one per kind: markers and their attributes, a speaker line split into `speaker`/`speech`, a thought, a recap, each persona field label, the metadata header, `Round N.`, and `plain` for unrecognised text.
- **Side colouring:** a Mafia speaker and a Law-abiding speaker from the same cast get different kinds; a speaker absent from `<setup>` gets `plain`; the cast list's own role text is classified consistently with the dialogue.
- **Pre-022 degradation:** a real pre-022 file yields markers and speech but **no** side kinds, and does not raise.
- **Human seat:** bolded via the writer's marker; bolded via `Welcome, <name>` inference when the marker is absent; the marker wins when both are present; not bolded when neither is available. Cover a Law-abiding human and a Mafia human.
- **Writer:** the human's `<player>` tag carries the attribute and no other emitted text changes (pin against the existing `tests/test_eval_transcript.py` expectations).
- **UI, headless via `App.run_test()`:** the body renders with spans preserved; a transcript containing `[`-bracketed text renders it literally; scrolling and the back keys behave exactly as before. **Assert span structure, not rendered colour** — colour is theme-dependent and a poor test subject, as spec 037 established.
- **Mutation-verify at least:** dropping the round-trip guarantee, colouring an unknown speaker, and letting the marker lose to the inference. Report which tests catch each.
- Full `uv run pytest -q` green; the current baseline is **1289 passed, 1 skipped**.
