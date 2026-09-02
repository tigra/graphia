<!--
Technical considerations for spec 040 — The Moderator's Closing Story.
HOW a model-written closing story is produced, bounded, shown, preserved and
made self-validating — and the two pre-existing faults it forces us to fix.
-->

# Technical Specification: The Moderator's Closing Story

- **Functional Specification:** [040 — The Moderator's Closing Story](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **This spec cannot be implemented until spec 039 lands on `main`.** It reads `GameState["private_diaries"]` and the `DiaryRecord` shape, neither of which exists on `main` — 039 is on branch `spec-039-ai-private-diaries`. It also builds on 039's `FakeLargeUnified` per-schema queue, its `day_trailer` restructuring of `_render_phases`, and its key-by-key `quality` block. Spec 028's `private_thoughts` is on `main` already, so the reflections half is available regardless.

> **Everything load-bearing below was verified against the installed code and the installed Textual/Rich, not inferred.** Three specialist reviews and one exploration produced **five corrections to their own briefs**; each is marked where it arises. This project's technical documents have repeatedly asserted things that turned out false, so anything unverified says so.

> **This spec fixes two pre-existing faults, because the story is what makes them fire.** A display fault that silently deletes bracketed text from what players say and can crash the ending outright; and an end-of-game input contract that makes the ending unreadable once it exceeds one screen. Neither is caused here. Both are named in the functional spec's scope rather than smuggled in.

---

## 1. High-Level Technical Approach

A new node writes one short story after the facts, from the two private channels no player has ever seen. The story is its own public `SystemMessage`, bounded and clipped at a paragraph boundary, preserved as its own `<story>` block in the eval transcript, and counted in the ledger so a run cannot silently produce no stories at all.

Six decisions shape it, all taken by the author:

1. **Its own message**, after the facts — not appended to them. Four tests get ported.
2. **Every AI player's** private material, dead and surviving.
3. **Both channels** — 039's whole-day diaries *and* 028's per-round reflections.
4. **Held to the record**, with the spec stating honestly that this is an instruction, not a guarantee.
5. **A stated cap, enforced on the story that comes back**, and **marked when it fires**.
6. **Failure means no story** — no placeholder, no error, nothing half-rendered.

And one taken during technical review: **no ablation flag.** ADR 011 §3 exempts display-only changes and names the spec-020 clock and spec-021 transcript labels as precedent — verified. The story changes no gameplay decision, no prompt an AI reads during play, and no metric. **The accepted consequence:** every measured game from now on pays for one extra heavyweight call carrying the largest prompt in the system, with no opt-out. That is a real cost on a 50-game baseline and it was accepted knowingly.

Affected: `src/graphia/nodes/endgame.py`, `src/graphia/graph.py`, `src/graphia/runtime/graph_builder.py`, `src/graphia/prompts.py`, `src/graphia/llm.py`, `src/graphia/state.py`, `src/graphia/ui/app.py`, `src/graphia/tools/eval_transcript.py`, `src/graphia/tools/blunder_eval.py`, `src/graphia/eval_ledger.py`, `evals/README.md`, `tests/conftest.py`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 A new node — `moderator_story`, between `end_screen` and `END`

**Not a fold into `end_screen`.** And explicitly **not** on spec 039's replay-safety argument, which does not transfer: there is no `interrupt()` between `end_screen` and `END`, and `end_screen` is terminal, so nothing can replay. Five other reasons carry it:

1. **The fold pays MORE transcript cost, not less** — the single best argument, and it reverses the premise the brief handed the reviewer. `eval_transcript:_render_endgame` concatenates **every** `SystemMessage` in the delta into one `<endgame>` block with no separator or label. A folded story lands there **indistinguishable from the facts prose**, failing the "clearly marked" criterion silently. Making the fold satisfy it means teaching the renderer to split one message list by position — a more fragile seam than a second branch.
2. **The career event is isolated.** `end_screen` emits `KIND_GAME_ENDED` at its own tail. A fold stands a 5–15s non-deterministic call between "the winner is known" and "the career event is emitted", inside one function. A separate node commits and checkpoints the career event a super-step earlier; the hazard is structurally gone.
3. **`end_screen` stays a pure deterministic string builder.**
4. **One fewer test to port** — `test_runaway_end_screen_contains_kill_log_and_roster` calls `end_screen` directly and asserts `len(messages) == 1`. A separate node leaves it green; a fold makes it five tests, not four.
5. **Progressive rendering.** With a separate super-step the facts are already on screen while the story's call is in flight — which is what makes §2.11's waiting beat bearable.

**Three arrival edges, one change.** `end_screen` is reached from `route_after_win_night`, `route_after_win_day` and the spec-023 runaway short-circuit in `route_after_night_open` — but it has exactly **one** outgoing edge. Replacing `end_screen → END` with `end_screen → moderator_story → END` covers all three by construction. It looks like three changes and is one; three tests are still warranted because there are three routers.

**The node is total.** It returns `{"messages": [story_msg]}` or `{}`, and never raises. It does **not** return `phase` — `end_screen` already set it.

### 2.2 The material, and why Ollama is the binding constraint

Sizing the channels at a large table over a five-Day game: the public history is 15k–45k tokens, the reflections ~5k, the diaries ~7k. Nova Pro would absorb it. **The Ollama provider would not** — it is a first-class gameplay provider (ADR 010, architecture §4) and typical locally-served models carry 8k–32k context. An ungoverned prompt fails **every game** there. That, not cost, is why this must be governed.

**Assembly order is load-bearing:** render the private block first, measure it with `nodes.day:_estimate_tokens`, then pass `story_token_budget − private_tokens` as the public history's budget. The private material is structurally protected and the public history is what gets cut — which matches the spec's own rationale that the story "earns its place from the private material".

| Slot | Source |
| --- | --- |
| `{outcome}` | `endgame:_winner_line(winner)` — **reused**, so the story's outcome and the facts message's cannot drift |
| `{roster}` | New helper: name, side, alive-or-dead, `(the person playing)` for the human seat, persona read via **`endgame:_persona_field`** |
| `{events}` | `endgame:_format_kill_line` per `kill_log` record — **reused** |
| `{private_record}` | New renderer over the shared cursor walk (§2.3) |
| `{context}` | `nodes.day:_render_context(messages, "", …)` |

**`_persona_field` is mandatory.** Its docstring records that `end_screen` is the first reader of persona fields out of checkpointed state, where the serde returns a `PlayerPersona` as a plain `dict`. `moderator_story` is the **second** such reader and will `AttributeError` on the dict shape without it.

**Pass a `speaker_id` matching no player (`""`) to `_render_context`.** Verified: the filter is `if private_to and private_to != speaker_id: continue`, so every whisper is dropped. **This is a privacy requirement, not a convenience** — the human's role-reveal whisper and the Mafia teammate intros must not enter a prompt whose output is shown to the player. The Moderator gets the conspiracy from the Mafiosos' *diaries*, which is the point of the feature.

**Give the endgame its own governors** — `story_context_window` (≈400) and `story_token_budget` (≈12000), config-threaded, because the budget is provider-dependent and an operator on an 8k local model must be able to lower it. Do **not** inherit the Day's `context_window=150`: that expresses "the recent discussion", and a retrospective often needs Day 1. **Stated trade-off:** trimming stays oldest-first, so a very long game loses early public *dialogue* while keeping every early *diary*. A head-and-tail keep was considered and rejected — it needs a second trimming implementation and an elision marker the model may narrate as an event, introducing a fabrication risk to solve a completeness problem.

**Excluded, each for a reason:** the rendered facts prose (derivable, and passing it invites paraphrase — the flat-summary failure of §2.6); private whispers (above); any private material for the human (**there is none** — both writers skip `is_human`, so "never claims to know what the person was thinking" is satisfied *structurally*, which is the strongest guarantee in the spec); per-Night pointing detail (reset each Night, so final state holds only the last — the Mafia's reasoning arrives via their diaries); `DIARY_WINDOW`; and citizens' `true_self` (empty by construction).

### 2.3 The cursor walk must be shared, not duplicated

028's channel carries **no ordering metadata** by design, so 039's `thoughts_before` cursor is the only thing that can order the two channels against each other. **Extract the walk** out of `nodes.day:_private_record_block` into a pure helper returning an ordered `list[tuple[kind, day, text]]`, consumed by both that function and the new endgame renderer.

The safety constraint: 039 applies its window **before** the merge deliberately (cursors partition the thought list, so dropping a diary removes only its own line), so the extracted helper must take the diary list **already windowed, or `None` for no window**.

Duplicating instead was considered — 039's own `_merge_private_diaries` / `_merge_private_thoughts` are deliberately duplicated. Rejected here because the cursor walk is the load-bearing correctness rule of the two-channel design and its failure mode is *"the story tells events in the wrong order and nothing raises"*.

**Two things must NOT be reused from 039:** `DIARY_WINDOW` (it exists so a *player* reasons from three entries; the Moderator is the one reader for whom the full diary is the point), and `_private_record_block` itself (it windows, and renders under a second-person label addressed to the writer — a third-party reader needs per-author attribution with kind and Day).

### 2.4 No vote reconstruction — on redundancy, and a correction

`nodes.day:resolve_vote` sets `active_vote: None` on **every** branch, discarding the ballots — confirmed. But no reconstruction is needed: executions and Night kills are **already structured** in `kill_log`, and **the ballots are already in the prompt** as public Moderator prose in `messages`. A regex reconstruction would re-derive, into a structured block, text the model is reading verbatim.

**Correction to the exploration's stated reason.** *"`eval_transcript` lives in `tools/`, which `nodes/` does not import"* is **false** — `nodes/setup.py` imports `_mask_names` and `_normalize` from `tools.repetition_experiment` at module level, with a docstring framing it as "IMPORTED (never reimplemented)". The import **is** available. Reject reconstruction on **redundancy**, not layering — a later reader who inherits the layering claim will reach the wrong conclusion about a different question.

### 2.5 The paragraph-preserving clamp, and the truncation marker

039's `_clamp_diary_entry` is `" ".join(text.split())[:DIARY_MAX_CHARS].rstrip()` — it folds **all** whitespace including `\n\n`. Correct there (a `<diary>` is an inline element); wrong here.

A separate clamp in `nodes/endgame.py`: normalise line endings; split on runs of two-or-more newlines; within each paragraph fold whitespace runs to one space and strip; drop empties; rejoin with exactly `"\n\n"`; then bound in **two** dimensions — at most `STORY_MAX_PARAGRAPHS`, and accumulate whole paragraphs while the running length stays within `STORY_MAX_CHARS`.

**Truncate at a paragraph boundary**, not a character cut. This is a difference in kind: a diary cut mid-word is read only by a model and a maintainer; **the story is the last thing the player reads**, and a mid-sentence cut reads as a bug.

| Constant | Value | Reasoning |
| --- | --- | --- |
| `STORY_PARAGRAPH_BOUND` | 4 | The number **stated** in the prompt, interpolated once — 039's `DIARY_SENTENCE_BOUND` discipline |
| `STORY_MAX_PARAGRAPHS` | 4 | The enforcement half, satisfying "applied when the story is taken in" |
| `STORY_MAX_CHARS` | ~1800 | Defensive backstop. **Keep under ~2500:** `llm:_OLLAMA_MAX_TOKENS = 1024` caps output, so a longer cap means the Ollama path truncates mid-sentence *before* the clamp sees it, and the clamp then cuts at a paragraph boundary and silently drops the ending |

Module constants, not config — the functional spec fixes the length, and ADR 011 asks for a flag, not a tunable (verbatim 039's argument for `DIARY_WINDOW`).

**Marking a clipped story (author-approved).** When the cap actually fires, the story message carries a second machine flag and the transcript element renders `<story truncated="true">`. Without it a reviewer judging the prose attributes a story that stops early to the storyteller rather than the cap — exactly the wrong conclusion when the point of preserving it is to judge it. **Deliberately NOT added to `_ATTR_NAMES`**, following spec 038's ratified treatment of `human="true"`: a machine flag is not a detail a reviewer reads, so it stays part of the tag's punctuation.

### 2.6 Prompts, schema, and the collapse this spec fights

**Schema** — `class Story(BaseModel): text: str` in `llm.py`. One flat primitive field, the sixth of the family, for the Bedrock Converse constraint. `with_structured_output`, never `bind_tools`.

**A hard constraint, verified:** the story message must be a **`SystemMessage`** (Moderator voice), **never an `AIMessage`**. `blunder_eval:_ai_lines_with_names` and `_ai_lines_with_speakers` both require `isinstance(m, AIMessage)` **and** `name in ai_names`. An `AIMessage` named after a player would inject several hundred words into the repetition pool and **corrupt the ledger**.

**Naming note:** there is `Story` (the schema), the `<story>` tag, and — deliberately — **no state channel**. The story lives only in `messages`. Worth recording, because every sibling private-material feature has a channel and a reader will look for `story_text` in `GameState`.

**The failure mode is different from 039's.** 039 fought **mode-seeking** (a checklist collapses every character onto one voice). 040 fights **the flat summary** — handed a fact sheet, attributed quotes and "stay to the record", a model produces a chronological recap of the facts the player just read. **That failure is created by the grounding instruction**, which is why both halves must be designed together.

Seven moves in `STORY_SYSTEM`, each load-bearing:

1. **Name the reader and what they already know** — "they have just read who won, who died, who everyone really was. They do not need those again." The strongest anti-recap move: it makes the recap *redundant to a stated audience* rather than merely discouraged.
2. **Name what they have never seen, and that it is the substance.**
3. **Grant the choice of thread in the same breath as the constraint** — 039's lesson: naming one direction steers everyone the same way; naming the *choice* makes variation legitimate.
4. **Forbid the fixed forms by name** — no day-by-day chronology, no per-player roll-call, no headings, no bullets. Naming *chronology* and *roll-call* is the analogue of 039 naming "summing-up-followed-by-a-plan": they are the two moulds a grounded storyteller falls into unprompted.
5. **State the record constraint as a boundary on NOUNS, not a demand for flatness** — "every player you name, every death and every vote you mention must appear below; within that, the telling is yours." Constraining *what may be named* rather than *how it may be told* is what stops the constraint becoming a summary instruction. **This is the sentence the acceptance criterion tests for.**
6. **The human clause as its own sentence**, so it is separately assertable.
7. **The length bound, stated once**, interpolated from `STORY_PARAGRAPH_BOUND`.

**Deliberately absent, with a "do not tidy this in" comment:** any enumeration of what to cover (that is the checklist, and the checklist is the collapse); and any word like *dramatic*, *vivid* or *compelling* — those invite embellishment, the risk this spec is most exposed to. Also absent: a persona for the Moderator, who has never had one.

**The runaway case must be handled in the wording.** `winner` can be `"runaway"` and defensively `"draw"`. The prompt must **not assume a winning side** — a story declaring a winner on an unresolved game is the most conspicuous fabrication available. Phrase the outcome reference as "how the game ended, below".

**Tier: `get_large()`.** Settled by architecture §4, which already names "the end-of-game creative recap (Phase 6)" as a use of the primary heavyweight tier.

### 2.7 Grounding, and what cannot be guaranteed

**What makes it likely:** the fact sheet anchors what may be named; the private material is quoted verbatim *with author, Day and kind*, so recorded motives are present and inventing one accidentally is hard; the votes are in the prompt verbatim; a single flat schema field leaves nowhere to smuggle an invented cast; and no tools, retrieval or web access exist. **The instruction itself is directly testable** — assert the record-constraint wording appears in the composed prompt.

**What cannot:** a name not on the roster; an invented private thought attributed to the human (structurally it cannot *quote* one, only the prompt clause stops it *inventing* one); embellishment of a real event.

**No automated grounding check, deliberately.** A name-whitelist rejection sounds cheap and is wrong: names legitimately appear inside personas and player speech, and the check would fire on "Moderator", on in-world place names, and on relatives a persona mentions. **Rejecting on a false positive produces the no-story ending — a worse outcome than an unpoliced adjective.** The spec's honest limit plus CR 005's effort-not-results principle cover this; the mitigation that fits is the one the spec already asks for — the story sits in the same transcript as the material it drew on.

### 2.8 The failure path — and no fallback text

| Layer | Rule |
| --- | --- |
| `_ai_story(...) -> str \| None` | Whole prompt build + invoke + validation + clamp in one broad `try/except Exception: return None` |
| **No fallback text** | **A deliberate asymmetry from 028/039.** Every sibling has a fallback constant. A placeholder story *is* the half-rendered ending the spec forbids, and an apology in all but name. `None` → node returns `{}` → no message. **Written down here because a reader will otherwise add one to match the pattern** — and because it is what makes §2.10's measurement cheap: presence *is* the signal |
| `moderator_story(...)` | Its own `try/except Exception: return {}` — belt-and-braces for a malformed `players` map or a persona shape surprise |
| Empty / whitespace-only | Treated as failure |
| `phase` | **Not returned** |
| Timeout | **Not handled, deliberately.** No gameplay node has one; adding it only here is a new mechanism. Honest consequence: a hung provider parks the ending, mitigated by the existing Esc / Ctrl+C path |

**Two career paths, not one.** The in-graph `career_emitter` emit is safe by node separation (§2.1). But `GraphiaApp._record_career` sits **inside `_drive`'s `try`**, after `await drive_graph(...)` — so a raising story node drops the UI-side career fold too, showing an error banner instead of an ending. The exploration missed this, and it is the **likelier** way the career data is actually lost. Node totality protects both.

### 2.9 The transcript — a `<story>` sibling block

**One top-level `<story>` block, sibling of `<endgame>`, emitted immediately after it and last inside `<transcript>`.** Not an extension of `_render_endgame`, for three reasons: two existing tests pin `<endgame>` as one labelled top-level block and stay **green unchanged**; `<endgame>` is the record of *facts* and the story is a *retelling* of them, so folding would rest "clearly marked" on an inner label inside a block a reviewer reads as the factual reveal; and `</endgame>` … `<story>` makes "at the end, after the final events" structural rather than conventional.

**Discriminate on the MESSAGE, not the node or the position.** The story message is a public `SystemMessage` carrying `additional_kwargs={CLOSING_STORY_KWARG: True}`, with the constant in `graphia.state` — which both `eval_transcript` and `blunder_eval` already import from and which drags in no gameplay stack. **This applies a 039 lesson:** `blunder_eval._diary_fallback_text` had to reach lazily into a *private* `nodes.day` constant and spends fifteen docstring lines apologising for it. A public contract constant costs nothing and avoids that entirely.

| Symbol | Change |
| --- | --- |
| `_ENDGAME_NODES` | **New** `frozenset({"end_screen", "moderator_story"})`, replacing the bare `node == "end_screen"` test. **This is the fix for the stray-`<preamble>` trap** |
| `_split_endgame_texts` | **New.** Partitions the delta's `SystemMessage`s into `(facts, story)` on the kwargs flag |
| `_render_endgame` | Signature-stable, input narrowed to the facts. Keeps `(no endgame recorded)` verbatim |
| `_render_story` | **New.** `_wrap("story", lines)` over the story split on `"\n"`, blanks trimmed; `None` when unusable |
| `_render_phases` | Emit `<endgame>` iff `facts or not story` (so a story-only delta produces no phantom empty block, while an all-empty delta keeps today's placeholder); then `<story>` iff `story` |

**Contract-stable:** for any capture with no flagged message the document is **byte-identical** to today's.

**The block form, not inline — decided by the tokenizer's invariants.** An inline element whose content spans lines would need a claimed multi-line body span to be marked at all, violating spec 038's "no styled span carries a `\n`". And traced: if left unclaimed it *still* breaks — the final line (`para2</story>`) fails the tag-head match at position 0, so **the whole line including `</story>` becomes one plain span** and the closing marker is lost. Degradation, not a crash, but the file is then ambiguous.

Two traps for the implementer: `_wrap` counts non-empty *entries* for its single-line collapse, so a one-entry body holding `\n` collapses to an inline element spanning lines — **split on `"\n"` in `_render_story`, never hand `_wrap` one entry with newlines.** And trim leading blanks, or `["", "one para"]` collapses and loses it.

**One elegant consequence, so nobody "fixes" it:** `_wrap` renders a single-paragraph story inline and a multi-paragraph one as a block — exactly how `<night>`, `<day>` and `<preamble>` already behave.

**Refinement to a rule the brief stated too strongly:** it is **no *styled* span** that may carry `\n`. The corpus sweep skips `KIND_PLAIN` before its newline check, and separators are emitted as `("\n", KIND_PLAIN)`. A block body whose lines are plain is fine.

### 2.10 The tag registry, and the ledger

**Register the tag, claim no body kind.** `_MARKER_TAGS + "story"` is **required** — without it the delimiters degrade to plain and read as prose. Zero corpus impact (measured: `<story` occurs 0 times across all 298 files). **No `_INLINE_CONTENT_KINDS` entry**: it fires only on the same-line path, so it would claim a one-paragraph story's body and *not* a multi-paragraph one — the same content kinded two ways depending on paragraph count, which is worse than no kind. **No new `KIND_*`, no `TRANSCRIPT_KINDS` entry, no `_ATTR_NAMES` change.**

So `<story>`'s body is `plain`, exactly like `<endgame>`'s — the format's existing precedent for a Moderator-voice block whose body is unstyled and whose marker is the landmark. "Clearly marked" is a requirement on **the file**, and the tag satisfies it; styling the body in the reading view is spec 038's domain and can be additive later.

**Two corrections to the palette brief, both measured.** The count is **five** variables clearing AA on both builtin themes (`$text`, `$text-primary`, `$text-muted`, `$text-error`, `$text-secondary`), of which three are non-neutral hues — "three" was the hue count. All five are assigned, so the conclusion stands. And **`underline` is now taken** by 039's `diary`. Spent axes: `bold`, `italic`, `reverse`, `underline`. Remaining: **`uu` alone** is genuinely live (`Style.underline2` exists); `overline` is **measured dead** (parses, but `textual.style.Style` has no such field, so it paints undecorated); `dim` is unusable stacked on `auto 60%`; `strike` reads as retracted; `background:` is stripped by `_kind_styles`. **Do not spend `uu` here** — the story has a unique once-per-file position, its own delimiters, and no competitor within a hundred lines. Position plus marker beats any remaining flag.

**The ledger gets a `quality.` trio, and nothing else.** No `metrics:` entry, no `METRIC_ORDER` change, **no `METRICS_VERSION` bump** (additive; no changed detection rule or denominator — the `outcomes` / `vote_activity` / `ci_low` precedent). A story-quality rate would invent the scorer this spec disowns; a length statistic is the first step of the same. No table column — nothing is paired.

```
quality:
  games_failed_early: 2
  story_missing_rate: 0.111      # games with no story / games that reached the ending
  story_missing_games: 2
  story_ending_games: 18         # THE DENOMINATOR — the rate is never written alone
  duration_seconds: 3841
```

**The argument is 039's, transferred.** 039 shipped `diary_fallback_*` because a smoke run measured the placeholder 9 times in 11 and *nothing said so* — not the ledger, not the transcript, not the log (the harness installs no logging handler). The identical failure is available here: a 20-game run whose story call failed 18 times produces a record indistinguishable from a clean one. **And with no ablation flag, absence in a post-040 record is unambiguous — it means failure** — which makes the signal *more* valuable, not less.

Flat scalars, no Wilson band (`quality` is a **census of one run**, not a sample — there is no sampling uncertainty for an interval to express), rate derived once at write time, gated on the **denominator** (`story_ending_games > 0`), and pathology-directed (`missing`, so a high number is visibly bad). Denominator defined as games whose event log contains an `end_screen` delta — computed from the capture, **not** assumed equal to `games_completed`; any divergence is itself informative.

The scorer is pure and offline-testable, and **must import `CLOSING_STORY_KWARG`** rather than copy a literal — 039's "IMPORT the thing, never copy it, so a reword breaks loudly in the offline suite instead of drifting a measurement".

**A genuine simplification over 039, worth recording:** 039 needed the fallback *text* because a failed diary is indistinguishable from a real one by presence. 040 chose absence-on-failure, so **presence is the signal** and no private-constant reach-in is needed. **If a placeholder story is ever added, this evaporates and 040 inherits 039's whole coupling problem.**

### 2.11 The UI — the exit contract, the focus fix, and two faults

**The headline criterion would have failed silently.** Verified: `CornerBadge.can_focus = False` and the header is a `Static`, so when the input is disabled at game end, focus falls to the **first focusable widget in DOM order** — `#private-log`, the six-line whispers pane. PgUp would scroll *that* while the public log stays pinned, and nothing visibly happens. **Explicitly focus `#public-log` when `_game_over` flips, on both the normal and `except` paths.** This is the single highest-value line of the change.

**Scrolling needs no framework work.** `RichLog` is a `ScrollView` with `overflow-y: scroll` and `can_focus = True`; probed at 120 lines in a 14-row viewport it reported `max_scroll_y=106` and responded to `up`/`pageup`/`home`/`end`. Keep `max_lines` unset — capping it is exactly what would push the winner line out of reach. Set `auto_scroll = False` at game-over so a late write cannot yank a reader halfway up the roster (measured: a write moved `scroll_y` 91 → 107).

**The exit contract.** `App.on_key` **still fires** for keys a focused widget's binding consumed, so "let scroll keys through and exit on everything else" is not expressible — the leaving key must be named positively. Bind **`q`** as an action-gated App binding, keep `escape` and `ctrl+c` working (both already leave and are advertised elsewhere), and **delete `on_key`** rather than narrowing it — two dispatch paths for one intent resolve version-dependently. Mirror `action_request_quit`'s `ModalScreen → SkipAction` guard, or `q` fires from under `FailureModal` and the player loses the only pointer to the remote log. **Do not use `enter`** — it is the submit key for every Day turn, and the player's last act before the ending is `enter`.

**Space exits today** and is what people press to page through text — Textual binds no page-down to it. Named explicitly in the criteria for that reason.

**The affordance must survive scrolling.** A banner scrolls away the moment the person scrolls up. Set `#public-log.border_subtitle` when `_game_over` flips — verified to add **zero** log lines — so the hint is pinned to the frame the whole time they read. Update the banner text and **both** error banners in lockstep; `"Press any key to exit."` becomes false. **No `Footer`** — there is none today, so `show=True` displays nowhere, and adding one docks a permanent row for the whole game.

**The waiting beat.** With `stream_mode="updates"` a chunk arrives only after a node returns, so the UI can arm on the facts and clear on the story — or on `drive_graph` returning, which covers the no-story path for free. **Use `border_subtitle`**, not a log line: `RichLog` has **no per-line removal**, only `clear()`, so any "one moment…" line is permanent and would sit between the reveal and the story forever. That is what the functional spec's clause — *nothing shown during the wait remains in the ending* — exists to rule out. `widget.loading` is rejected: it replaces the widget's content, hiding the facts the person is meant to be reading. Token-level streaming is the right eventual answer and belongs with the Phase-6a `astream` switch.

**The markup fault, measured.** `_write_public` interpolates the body into a markup string and calls `Text.from_markup`, with **no `rich.markup.escape` anywhere in `app.py`**:

```
'he called it [gibberish] and moved on'  →  'he called it  and moved on'   (silently deleted)
'sarcasm [sic] noted'                    →  'sarcasm  noted'
'a stray [/bold] close'                  →  MarkupError
'The baker [Aarav] knew'                 →  survives (uppercase)
```

The silent deletion is the insidious half — words vanish with no error and no trace. The `MarkupError` half propagates `_write_public` → `_handle_graph_message` → `_consume_stream` → `drive_graph` → `_drive`'s `except`, so **`_record_career` is never called** and a red banner replaces the ending: it defeats the designed no-error failure path from the UI side where the node's guards cannot reach, and violates three of this spec's own criteria at once.

**Take spec 038's answer: spans, not markup.** `RichLog(markup=False).write(Text.assemble(("Moderator: ", "bold cyan"), (body, "")))` — verified to render `[sic]` and `[/]` literally. **Apply it to all message bodies, not just the story:** the exposure is identical for AI speech and whispers, and fixing only the story leaves the same landmine on the path it has always been on. Nothing is lost — no message body in `prompts.py` or `nodes/` contains markup, and the 298 committed transcripts contain zero lowercase-bracket sequences. Keep the project's own literals (labels, hints, banners) on `from_markup`. Belt-and-braces at the point of no return: a narrow `try/except` → plain-`Text` fallback around the story write.

**The runaway path.** No career panel (`"runaway"` is deliberately absent from the outcome map), so the story is the last content before the banner. **The exit contract must not differ** — a second contract for one ending variant is a bug factory and makes "it is clear which key leaves" conditional. Two consequences: a clipped story has nothing after it to soften the edge, which is an argument for §2.5's paragraph-boundary truncation; and there is a **third** no-panel path, `_drive`'s `except`, where the border subtitle should still be set because a crashed ending is also something the person may want to scroll.

### 2.12 No flag, and the accepted cost

ADR 011 §3 exempts display-only changes and names the spec-020 clock and spec-021 transcript labels as precedent — verified. The story is post-game, changes no decision, no prompt read during play, and no metric. **So: no `GRAPHIA_*` flag, no `_assemble_graph` parameter for it, no header arm part, no `settings` key, no arm to label.**

**Cited explicitly so the absence is a recorded decision rather than an omission** a later reviewer flags. And stated plainly: **every measured game now pays for one extra heavyweight call carrying the largest prompt in the system, with no opt-out.** On a 50-game Bedrock baseline that is material, and it was accepted knowingly.

The two size governors (`story_context_window`, `story_token_budget`) **are** config, threaded through both builders — they are provider-capability knobs, not an ablation arm, on spec 025's precedent for `context_token_budget`.

---

## 3. Impact and Risk Analysis

- **`safe_llm` does not net `graphia.nodes.endgame`, and neither does `fake_large`** — verified: `safe_llm` patches `nodes.setup`'s three, `nodes.night.get_large`, `nodes.day.get_large` and `blunder_eval.get_embeddings`, and nothing else. A `get_large` import in `endgame` reaches **real Bedrock from the offline suite**, and the boto3 retry loop keeps an `asyncio.to_thread` worker alive past `app.exit()`, hanging pytest teardown. **A prerequisite, the first task of the first slice.**

- **A missing fake queue is a *silent* failure** — the house `try/except → fallback` posture swallows `FakeLargeUnified`'s unknown-schema `AssertionError`, so the test measures the no-story path and passes green. 039 documented this trap explicitly. Add `Story` to the queue and `calls_by_schema` **in the same change** as the call site, and assert the counter in every story test. **Inverted here:** absence is *also* the correct failure outcome, so a test must distinguish "absent because the queue raised" from "absent because the fake was never reached".

- **Four tests to port** — three in `test_slice8_endgame.py` and one in `test_personas.py` read `system_msgs[-1].content` expecting the facts. **Port by selecting on content** (the message containing `ENDGAME_HEADER_ROSTER`) rather than by index — more durable. `test_personas.py`'s inverted assertion (`system_msgs[:-1]` must not contain the persona header) fails twice over and needs the same treatment. `test_slice9_polish.py`'s direct-call test **stays green**.

- **Four end-of-game UI tests look like they cover "any key exits" and cover nothing** — their `is_running` assertion sits **outside** the `run_test()` block, where the pilot has already shut the app down. Proven: patched to a `q`-only contract, all 38 tests still passed. **Repoint them and move the assertion inside**, or the new contract ships unverified. One also carries a docstring that is factually wrong about this Textual version.

- **The stray-`<preamble>` trap** — without `_ENDGAME_NODES` the story is correctly *positioned* and wrongly *labelled*, nothing raises, every existing test green. Make its test a Slice-1 deliverable.

- **Model prose containing a tag-shaped line** could emit a literal `</story>` and break the block boundary for a future judge parser. The reader degrades to plain and never raises, but the file is ambiguous. Drop or neutralise such a body line in `_render_story` — the single choke point. **Narrower than 039's clamp: structure only, wording untouched**, so spec 022's "the renderer never rewrites the wording" holds.

- **The reviewer's cross-reference is satisfiable but incomplete.** The story and its sources are in one file — but 039's finding is that **the Day the game is won has no whole-day diary**, and that is the day a story most wants to land its reveal on. A reviewer checking the climax must read the `<thought>` elements of the final Day, and nothing in the file signals that. State it in `evals/README.md`'s transcript section, where a reviewer will actually look.

- **Append-only ledger** — the trio must **never** be backfilled: a pre-040 record was played by a build with no story path, so it has no denominator. Absence is a *third case*, not a falsy one.

- **Transcript hygiene** — a smoke run to see the new block writes an untracked dir, which makes the **next** eval stamp `code.dirty: true`. Clean or commit before the first measured run. One committed keeper showing a real multi-paragraph story beside its diaries is worth having — decide that deliberately.

- **Cost** — §2.12. Accepted, no opt-out.

- **No new dependency, no new AWS resource, no IaC change.**

---

## 4. Testing Strategy

- **Prerequisite first:** `safe_llm` + `fake_large` net `nodes.endgame`; `Story` joins `FakeLargeUnified`; a regression test that a flag-on run produces the **scripted** story, not silence.
- **The node:** one story message per game, on all **three** arrival routers; it is a `SystemMessage` with no `name` (and a test that `_ai_lines_with_names` excludes it — the ledger-corruption guard); `phase` untouched; the career event still emitted.
- **The failure path:** a raising queue yields exactly the facts message, `phase == "end"`, the career event emitted, `next == ()`, **and `calls_by_schema[Story] == 1`** so absence-by-failure is distinguished from absence-by-never-reached.
- **The clamp:** paragraphs survive; a four-paragraph story is untouched; a five-paragraph one loses the last whole paragraph, not a mid-sentence cut; `truncated` is marked only when the cap fired.
- **The prompt:** the record-constraint sentence and the human clause are present in the composed prompt; the private material is attributed with author, kind and Day; **no whisper text appears**; the outcome reference does not assume a winner on a runaway.
- **The transcript:** `<story>` is its own top-level block after `</endgame>`; multi-paragraph renders as a block and single-paragraph inline; **no story ⇒ document byte-identical**; a story-bearing delta from an unrecognised node yields no `<preamble>`; an adversarial `</story>` in the body is neutralised; the corpus round-trip stays green over all 298 files.
- **The ledger:** the trio is absent when no game reached the ending, present with a `0.0` rate on a clean run, and gated on the denominator; `METRICS_VERSION` unchanged; the scorer imports the shared constant.
- **The UI, via `App.run_test()`:** focus is on `#public-log` at game-over; `q` and `escape` leave; `x`, `space`, `up`, `pageup` do not; `pageup` decreases `scroll_y` and `home` reaches the winner line; the affordance names the key and survives scrolling; the waiting beat adds no log lines; **a story containing `[sic]` and `[/]` renders literally and the career panel still appears with no error banner** — the regression test for the whole markup chain; runaway parity.
- **Mutation-verify** at least: the missing `_ENDGAME_NODES` branch, a whitespace-folding clamp, an `AIMessage` story, a truthiness-gated ledger trio, and the markup path restored.
- Baseline to hold: the suite green at the count 039 leaves it.

---

## 5. Open decisions for the author

1. **Whether to commit one keeper transcript** showing a real multi-paragraph story beside its diaries, as corpus evidence for the future judge. Recommended, and cheap — but it is a deliberate addition to a curated store.
2. **Whether the shared cursor walk is extracted (recommended) or duplicated.** Extraction means 040 touches 039 code, which is only safe post-merge.
