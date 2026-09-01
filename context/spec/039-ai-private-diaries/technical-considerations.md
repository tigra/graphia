<!--
Technical considerations for spec 039 — Per-AI Private Diaries.
HOW a before-Night diary is written, interleaved with spec 028's thoughts,
persisted twice, preserved in the transcript, and measured.
-->

# Technical Specification: Per-AI Private Diaries

- **Functional Specification:** [039 — Per-AI Private Diaries](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **Spec 028 is the template, and its §2.1 argument does not transfer.** Diaries are structurally parallel to Day-round thoughts, so [028's technical document](../028-ai-private-thoughts/technical-considerations.md) is the shape to follow. But 028 justified its dedicated node by **replay safety** — `day_turn` is the interrupting node, so folding N model calls into it would expose them to re-execution after a human `interrupt()`. **At the Day→Night hinge there is no interrupt until `mafia_point`, several committed super-steps later, and LangGraph replays only the interrupted node's own task.** Folding into `day_close` would *not* be replay-unsafe. The case for a dedicated node here is different and is argued on its own terms in §2.1. Do not borrow 028's wording — a reviewer who checks will find it false.

> **Everything load-bearing below was verified against the installed code, not inferred.** Where a claim could not be verified it says so. This project's technical documents have repeatedly asserted things about the codebase that turned out wrong; each verification is named.

---

## 1. High-Level Technical Approach

Before each Night, a new dedicated node writes one diary entry per surviving AI player. The entry lands in **two** places — a new `GameState` channel (which is what reaches the prompts and the eval transcript) and the existing `DiaryStore` (which is the AgentCore Memory demonstration the roadmap item exists to exercise, replacing a placeholder that has stood since spec 002).

Five decisions shape the work, all taken by the author before drafting:

1. **A separate parallel state channel**, not an extension of spec 028's `private_thoughts` — which stays untouched.
2. **Dual write**, replacing `night_close`'s `"Night N diary placeholder for …"` — whose own comment reads *"Phase 6 will replace it with the AI's actual private reflection."* This spec is that item.
3. **Diaries and thoughts interleave** into one private record, in event order.
4. **A window of the three most recent** diaries reaches decisions; every entry is preserved.
5. **A default-on ADR-011 flag**, and a recorded six-run comparison.

Affected files: `src/graphia/state.py`, `src/graphia/llm.py`, `src/graphia/prompts.py`, `src/graphia/nodes/day.py`, `src/graphia/nodes/night.py`, `src/graphia/graph.py`, `src/graphia/runtime/{graph_builder,__main__}.py`, `src/graphia/config.py`, `src/graphia/tools/{eval_transcript,blunder_eval}.py`, `src/graphia/eval_ledger.py`, `evals/README.md`, `tests/conftest.py`. **Verified unchanged:** `src/graphia/tools/inspect_diary.py` (confirmed by execution in Slice 3, not by reading — real prose with quotes, an apostrophe, an em-dash and embedded newlines round-trips through `AgentCoreMemoryDiaryStore` and decodes correctly). **`src/graphia/diary_store.py`'s CODE is unchanged, but its module docstring is not** — corrected during Slice 3: it opens with *"In Slice 6 the entries are placeholder text; Phase 6 will replace them with the AI's real diary content"*, which this spec ends. It is the first thing anyone opening the store reads, so it is a task, not a note.

**Naming, reconciled.** The two specialists proposed different names. This spec uses the **spec-028 sibling spelling** throughout, because one spelling from env var to ledger key is worth more than either alternative: flag `GRAPHIA_PRIVATE_DIARIES`, config field `private_diaries_enabled`, state channel `private_diaries`, ledger key `settings.private_diaries_enabled`. **Not** `GRAPHIA_DIARIES` — too close to the pre-existing store concept.

**A name collision that will otherwise cost an hour:** `graphia.diary_store.DiaryEntry` already exists. Three distinct names for three distinct things — `Diary` (structured-output schema, `llm.py`), `DiaryRecord` (state entry, `state.py`), `DiaryEntry` (store DTO, unchanged).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The node — `day_diary`, dedicated, on the Day→Night hinge

`day_close → day_diary → night_open`, two unconditional edges replacing the current one. Lives in `src/graphia/nodes/day.py`.

**Why a dedicated node** (not 028's replay argument — see the note above):

1. **Delta attribution.** `eval_transcript:_render_phases` dispatches on the `{node: delta}` key. Folded into `day_close`, the diary would arrive in the same delta as the Day's closing recap, forcing the renderer to split one delta by channel and pinning placement to wherever those messages land.
2. **Failure isolation in the useful direction.** A fan-out raising past its per-player guard would take `day_close`'s already-computed closing recap down with it. A separate node fails *after* the Day's public close is committed.
3. **`day_close` is currently pure and deterministic.** Adding N model calls makes one node do two unrelated things and couples the public recap to model latency.
4. **Future-proofing.** The moment anyone adds an interrupt to `day_close` — a "press enter for Night" beat is a plausible Phase 6a ask — a fold becomes genuinely unsafe.

**Why not `night_open`'s head** — three independent blockers, all verified: it bumps the cycle on re-entry so `state["cycle"]` is already the *new* Night's; Night 1 arrives there from `first_night_mafia_intros` and would need a special case; and it carries the spec-023 runaway short-circuit.

**Self-guards**, mirroring `nodes/day.py:day_round_reflect` — return `{}` when the flag is off, when `state.get("winner")` is set, when the runaway cap is about to fire, or when no surviving non-human player exists. Otherwise iterate `players.values()` filtered on `is_alive and not is_human` — the eval scripted seat is the **human** seat, so it is excluded with no special case.

**The runaway guard is not optional.** The node runs *before* `night_open` detects `cycle + 1 >= max_days`, so the last Day of a runaway game would fire a full fan-out for a Night that never happens. Thread `max_days` into the `day_diary` partial **from the same `_assemble_graph` value that binds `night_open`** — one value bound twice, so no second constant and no drift. **Weaker than it sounds, noted during implementation:** sharing the constant removes *constant* drift, but the **predicate** is duplicated — `day_diary` hard-codes the knowledge that `night_open` bumps because the phase is `"day"`. Change `night_open`'s bump condition and the two diverge silently with the shared constant intact. The durable fix is a shared pure predicate (e.g. `would_hit_day_cap(cycle, max_days, prior_phase)`); out of scope here, documented in the node's docstring.

**Three boundary conditions to state, because each looks like a bug when counting entries in a preserved game:**

| Condition | Consequence |
| --- | --- |
| **Night 1** | No preceding Day ⇒ no entry. N−1 entries over N Nights. |
| **The winning Day** | `check_win_day` routes to `end_screen`, bypassing `day_close` entirely — the Day the game is won produces no diaries. |
| **Runaway cap** | Guarded above; the final Day of a capped game produces none. |

**Why `nodes/day.py` and not a new module:** `tests/conftest.py:safe_llm` already patches `graphia.nodes.day.get_large`. A new module means a new patch target — the exact "forgotten stub falls through to real boto3 and hangs pytest teardown on retry loops" failure 028 flagged as a hard prerequisite. `day_diary` edging into `night_open` is fine; `day_close` already does.

### 2.2 State — the channel and its entry shape

```
private_diaries: Annotated[dict[str, list[DiaryRecord]], _merge_private_diaries]

class DiaryRecord(TypedDict, total=False):
    day: int              # the Day cycle this entry sums up (== state["cycle"] at write)
    thoughts_before: int  # the interleave cursor (§2.3)
    text: str
```

**A `TypedDict`, not a dataclass — this is a hard constraint, not a preference.** `graph.py:make_checkpoint_serde` allowlists exactly `[PlayerState, PlayerPersona]` and, per its own docstring, that allowlist *switches off* LangGraph's permissive warn-and-allow default. Any new custom class in `GameState` is rejected by that allowlist unless added there. **Corrected during implementation — the failure is worse than "blocked" suggests, and it does not raise.** Read in the installed LangGraph: the ext hook logs a warning and then `return tup[2]` — *the raw kwargs dict* ("We default to returning the raw data"). So a dataclass `DiaryRecord` would come back from a checkpoint as a bare `dict`, and `record.text` would fail with `AttributeError` **only after an interrupt/resume** — invisible to every test that never resumes. That silent-degradation-on-the-resume-path-only shape is the real argument for the `TypedDict`, and it is what the code comment should say. `KillRecord` and `ActiveVote` are `TypedDict`s for precisely this reason. State the reason in the code so nobody later "improves" it into a dataclass.

`_merge_private_diaries` is a new module-level reducer, structurally identical to `_merge_private_thoughts`: shallow-copy the prior map, concatenate per key, mutate neither input, iterate insertion order only, never a `set`. **Do not generalize `_merge_private_thoughts` to serve both** — decision 1 keeps 028's channel untouched, and its reducer is part of that channel. Accept ~6 duplicated lines and cross-reference in the docstring.

### 2.3 The interleave cursor — how to merge without touching spec 028

The functional spec requires diaries and thoughts to appear in event order. Thoughts are bare strings with **no ordering metadata**, so there is nothing to merge on.

**Each diary records `thoughts_before` — the length of that player's thoughts list at write time.** Merging then walks the diary list in order, emitting every not-yet-emitted thought at index `< k_i` before diary *i*, and the remainder after the last.

Why it holds under the reducer and replay model:

- **Deterministic capture** — the node reads committed post-reducer state, so `k` is a function of committed state alone. No wall clock, no RNG. A replay recomputes the same `k`.
- **Monotone by construction** — thoughts only accumulate (`_merge_private_thoughts` concatenates, never truncates) and diaries are written in super-step order, so `k` is non-decreasing. The merge is a stable two-way merge, not a sort.
- **No co-write hazard** — `day_round_reflect` and `day_diary` are distinct nodes, never in the same super-step, and the graph has no parallel fan-out writing either channel.
- **Cross-flag safe** — with thoughts off, `private_thoughts` stays `{}`, every `k` is 0, and the merge degenerates to diaries-only. Still exact.

Alternatives checked and rejected: a global sequence stamped on both channels (edits 028's channel); wall-clock timestamps (non-deterministic — breaks `test_dual_mode_smoke.py`'s byte-equality and replay stability); `(cycle, round)` coordinates on diaries only (cannot order against an untagged list, and the count of thoughts per Day varies because an execution or the vote cap can close a Day early); positional derivation at render time (same defect).

The cursor is therefore **necessary and sufficient**. Document it as a cross-channel cursor.

### 2.4 The prompt seam — one merged block in the existing slot

**Render the merged record into the existing `{private_thoughts}` slot.** A fourth slot is impossible here, and not only for churn:

| Option | Blast radius | Can it interleave? |
| --- | --- | --- |
| 4th `{diaries}` slot | `KeyError` at **16** `.format()` call sites (verified — larger than the "~13" first estimated) | **No** — two slots are two positions; event order across them is unrepresentable |
| Separate appended message (031/034 precedent) | None | **No** — same problem, worse |
| **Merged block in the existing slot** | **None** | **Yes** |

The 031/034 precedent solves *adding independent content*; this requires *merging with existing content*, which only a shared slot expresses. **Do not rename the slot** — `blunder_eval:_speaker_anchor` derives its speaker-resolver anchor from `DAY_SPEAK_USER_TEMPLATE`'s literal prefix.

A new builder **delegates** rather than replacing:

```
_private_record_block(thoughts, diaries, *, thoughts_enabled, diaries_enabled) -> str
```

**When there is no diary to show — flag off, or none written yet — it returns `_private_thoughts_block(thoughts, enabled=thoughts_enabled)` verbatim.** Byte-identity to 028 then holds *by construction*, not by assertion. `_private_thoughts_block` keeps its signature, docstring, unit tests and its remaining caller `_ai_reflect`.

With diaries present, one list under the unchanged `PRIVATE_THOUGHTS_LABEL`, **only the diary lines tagged**:

```
Your private notes so far (yours alone):
- <thought>
- <thought>
- [Diary — end of Day 1] <entry>
- <thought>
- [Diary — end of Day 2] <entry>
```

Thought lines stay bare, so the diaries-on delta is *purely* the inserted lines. Export `DIARY_LINE_MARKER` as a module constant — the structural marker tests assert on. **It must be the invariant tag PREFIX, never a `"…{day}]"` template:** the analogy with `PRIVATE_THOUGHTS_LABEL` breaks because the tag embeds a varying day number, and a template constant makes every flag-off `DIARY_LINE_MARKER not in prompt` assertion **vacuously true**, since a literal `{day}` never appears in a rendered prompt. Same vacuity family as the f-string bound test.

**The cross-product is new contract surface:**

| `GRAPHIA_PRIVATE_THOUGHTS` | `GRAPHIA_PRIVATE_DIARIES` | Slot content |
| --- | --- | --- |
| ON | OFF | Exactly today's block — byte-identical via delegation |
| OFF | OFF | `""` — byte-identical to pre-028 |
| ON | ON, no entries yet (all of Day 1, every game) | Exactly today's block — **the change is invisible until the first diary exists** |
| ON | ON, entries exist | Merged, interleaved, tagged, windowed |
| OFF | ON | Heading + tagged diary lines only. **Corrected: `k == 0` here is a coincidence of a whole-run flag, not an invariant** — entries written while thoughts were ON carry `k > 0`, and flipping the flag mid-campaign or resuming a checkpoint written under the other setting yields cursors pointing at notes that are not there. The invariant the code actually needs is that **the cursor is clamped to the notes in hand**. |

**028's flag-off parity tests pass unchanged and must not be weakened** — verified: they assert `PRIVATE_THOUGHTS_LABEL not in prompt` and the note text absent, *not* byte-equality against a pre-028 rendering, and they call the helpers directly where `diaries` defaults to `[]`. But state the composed contract explicitly: **from 039 on, reproducing pre-028 prompt bytes requires both flags off**, because the two features share one slot. Add one test per cell above.

**FOUR call sites, not three — corrected during implementation.** `_private_thoughts_block` has five callers, and they do not all want the same thing:

| call site | gets the merged block? |
| --- | --- |
| `_ai_day_action` (Day speech) | **yes** — functional-spec §2 requires it |
| `_ai_ballot` (vote) | **yes** — required |
| `night.py:_ai_pick_target` (Mafioso Night pointing) | **yes** — required |
| `_ai_diary` (the diary prompt itself) | **yes** — §2.6 says the diary prompt sees "the player's own running private record", which after this slice means diaries too. Without it a player writing Day 3's entry has never read Day 1's or Day 2's, which undercuts the settled-read-carried-forward the feature rests on. **This also needs a state read added: `day_diary` does not currently pass `state["private_diaries"]` into `_ai_diary`.** No new `_assemble_graph` bind — `day_diary` already receives the flag. |
| `_ai_reflect` (spec 028's Day-round reflection) | **no, deliberately.** §3 Out-of-Scope keeps 028's reflections at "their own place in the player's reasoning"; feeding them diaries would change what a completed spec's prompt sees. |

Each site gains `diaries: list[DiaryRecord] | None = None` and `diaries_enabled: bool = True`, defaulted so existing direct test calls stay valid — the convention 019/024/025/028 used. Each passes **only the acting player's own** entries, keyed at the call site.

### 2.5 The window — in the builder, once

`DIARY_WINDOW = 3` as a module constant in `nodes/day.py`, applied in `_private_record_block`. **Call sites pass each player's FULL list; the builder is the sole point of truncation** (clarified during Slice 2 — the original wording ruled the window *out of* the call sites, which reads as merely undesirable rather than wrong, and a verification pass tripped on seeing 78 calls handed >3 records while every rendered prompt showed ≤3). Not at write (destroys "every entry is still kept"), not at the four call sites (four copies of one constant, one drifts and one decision silently sees a different window).

**Window before merging, not after.** Slice to the last three diaries, then merge. Correct because thoughts are indexed independently: dropping the oldest diary removes only its own line, and the survivors' cursors still partition the thought list.

**A pre-existing asymmetry to record, not fix:** 028 carries **all** thoughts with no window, and spec 025's `context_window` / `context_token_budget` govern only `_render_context` (verified). So the merged block is unbounded in the thoughts dimension. That is a 028 property this spec inherits; §3 Out-of-Scope forbids changing it. Worth a follow-up candidate. Keep `DIARY_WINDOW` a constant, not a config knob — the functional spec fixes it at three, and ADR 011 asks for a flag, not a tunable.

### 2.6 Schema, prompts, and the bound

**Schema** — `Diary(entry: str)` in `llm.py`, beside `Reflection`. Flat, one primitive field, per the Bedrock Converse constraint that shapes every existing schema.

**Prompts** — `DIARY_SYSTEM` + `DIARY_USER_TEMPLATE` in `prompts.py`. A *new* template has no `.format()` blast radius, so it takes whatever slots it needs; mirror `REFLECTION_USER_TEMPLATE`'s vocabulary so the same helpers populate it, including `{private_thoughts}` (the merged record — the diary prompt sees the player's own running record, the same self-grounding `_ai_reflect` does).

**Expressing "open enough that different personas use it differently"** — this is the mode-seeking failure spec 034 diagnosed for personas: given a checklist, the model produces one shape. Four moves, all in `DIARY_SYSTEM`: name both directions and grant either ("look back over the day, or look ahead to tonight, or both, whichever is more like you"); anchor on voice, leaning on the persona block already present; forbid a fixed form (no headings, no checklist, no summary-then-plan); and keep 028's anti-steer clause. **Do not enumerate topics to cover** — an enumeration is exactly what collapses every persona onto one shape and would fail the third acceptance criterion.

**The bound needs enforcement, not a prompt sentence.** 028's cap is prose only (`"a SHORT private note (one or two sentences)"`, unenforced and untested), and the functional spec's criterion — *"when its length is checked, then it does not exceed the stated bound"* — is not satisfiable by wording. Therefore:

- `DIARY_SENTENCE_BOUND = 6` in `prompts.py`, interpolated into `DIARY_SYSTEM` so the stated bound and the constant cannot drift.
- `DIARY_MAX_CHARS` in `nodes/day.py` (≈900), applied as a hard clamp on the accepted entry **before both writes**.
- **Leave `REFLECTION_SYSTEM`'s bytes alone.** The "larger of the two" criterion is checkable without editing 028: a test reads `DIARY_SENTENCE_BOUND` and asserts `REFLECTION_SYSTEM` contains "one or two sentences".
- `_DIARY_FALLBACK`, a deterministic note mirroring `_REFLECTION_FALLBACK`, on model failure or empty entry.

**Do not inherit a latent 028 defect:** `_ai_reflect` calls `_standings_prompt_block(state, enabled=True)` with the flag **hard-coded**, so spec 019's ablation is incomplete on that path. `day_diary` must thread `recap_aware_reasoning_enabled` through properly. Fixing `_ai_reflect` itself is out of scope; log it as a 028 follow-up.

### 2.7 The dual write

**Order per player:** build prompt → model call (guarded) → clamp → `try: store.write(...) except Exception: logger.exception(...)` → accumulate into the delta. The delta returns once, after the loop. **A store failure can never prevent the state write** — state is the gameplay and transcript source of truth; the store is the persistence side-channel. This is the posture `night_close` already documents: *"Persistence must never crash gameplay: catch broadly, log, and continue so one player's failure doesn't skip the rest."*

**Injection** follows the project's own *service-injection pattern* — `graph.py:_assemble_graph` binds `night_close` with `partial(night_close, diary_store=…, game_id=…)`; `day_diary` gets identical treatment. Keep the impurity in one `_persist_diary(...)` helper so the prompt build, merge and window stay pure and unit-testable.

**`night_index` arithmetic — get this right.** `night_close` writes `night_index=cycle` where `cycle` is the *already-bumped* Night. `day_diary` runs at Day cycle N, before the bump. To preserve the store's numbering semantics and keep `read()`'s `sorted(key=night_index)` meaningful: `night_index = record["day"] + 1` — the Night the entry precedes. Entries occupy nights 2..N. **Do not number by the Day summarised**, which silently redefines the field and shifts what `inspect_diary` prints.

**Removing the placeholder.** Delete only `night_close`'s second loop — the one whose comment names Phase 6. Two things survive:

1. **Keep the read-back loop.** Its stated purpose is superseded (the AI now reasons from state, not the store), but it is the **only** thing exercising `DiaryStore.read` in a live game, and in remote mode that is the whole Gateway → Lambda → Memory round-trip. Retarget its docstring to "liveness probe of the Gateway-fronted read path". *The author may reverse this.*
2. **`inspect_diary` needs no change** — verified: it decodes on `body["kind"] == "diary_entry"`, produced by `AgentCoreMemoryDiaryStore.write`, whose signature is unchanged. Only `content` changes from placeholder to prose; prose is `json.dumps`-encoded, so newlines and quotes are safe, and on the Gateway path it is an MCP string arg.

**An honest asymmetry to state:** with the flag off, no diary reaches the store at all — the placeholder does not come back. Flag-off is identical in everything ADR 011 is about (prompts, public messages, transcript) but **not** byte-identical in the store.

### 2.8 Transcript — a day-level trailer

`_render_phases` has nowhere to put day-level content after the rounds: `flush()`'s `"day"` branch is a header plus one `<round>` per non-empty body. A `day_diary` delta would otherwise land inside the **last `<round>`** — defensible but wrong in kind.

Add a `day_trailer: list[str]` accumulator, reset alongside `day_round_bodies`, appended after the rounds loop and before `_wrap("day", body)`. Diaries then render between the last `</round>` and `</day>` — literally *"between the day it was written about and the Night that followed"*. Render via the existing `_inline_attr`:

```
<diary player="Ava" day="2">…</diary>
```

Because `_append_diaries` is handed `day_trailer` rather than `current_day_body()`, it does not interact with `pending_round_break` — **note the property comes from the call site, not merely from the list being separate**, so a future editor who "simplifies" the call to `current_day_body()` would keep the separate list and lose it.

**An entry must be single-line, and the clamp is where that is enforced** (found in Slice 1): `_inline_attr` promises a single-line element, and `DIARY_SENTENCE_BOUND = 6` invites six sentences where spec 028 invited one or two — so a model emitting a blank line between thoughts would produce the format's **first** multi-line inline element (verified: zero exist across the 298 committed transcripts). Normalise internal whitespace in `_clamp_diary_entry` — **the broad reading: fold every whitespace run to a single space**, not merely replace newlines, which would leave `"a\n\nb"` as `"a  b"`. Fold before the cap, so the cap measures what is actually stored, **not** in the renderer: the clamp is the single point where an entry is accepted, so it covers the state channel, §2.7's store write and the transcript at once, whereas a renderer fix would leave state and store disagreeing with the transcript. The degradation is graceful either way — the tokenizer splits on `\n`, so an unmatched open tag reads as plain text and the round-trip invariant holds — which is why this is robustness rather than a defect.

**`day` is the one case where "defensive" carries a design choice** (settled in Slice 1): every other bad input resolves to "skip the entry", but a missing or nonsensical `day` still leaves an entry worth showing — so the **attribute is omitted and the element still rendered**, on the same reasoning §2.10 gives for rendering an absent ledger arm blank rather than `false`. Slice 4's attribute rule must accept a `<diary>` carrying only `player`. Defensive house style throughout: missing channel, non-dict, non-list, non-string, unknown id — never raise.

**Spec 038's highlighter must learn the tag too**, or a diary renders as undifferentiated body text: `eval_ledger` needs `KIND_DIARY`, a `_DIARY_TAG`, and entries in **`_MARKER_TAGS`** (is this line skeleton?) and **`_INLINE_CONTENT_KINDS`** (tag → body kind) — *corrected during Slice 4: the tech spec's `_TAG_KIND` names no symbol in the module; those two tables are what it meant.* Plus the attribute rule: **the owner `player` name is side-bearing** (ratified in Slice 4 — §2.8 prescribed the body and the `day` value but was silent here). It follows `_attr_kind`'s own rule, *the side colour lands on whichever token tells the reviewer the side — the role where a role is written, the name where none is*: a `<diary>` names a person and a Day but no role, exactly like `<thought>`. The achromatic alternative would render the same player's name two different ways a few lines apart in one file. One `elif` to reverse; no CSS depends on it, since `attr-mafia`/`attr-law-abiding` already exist. **`day` joins `_ATTR_NAMES`, which is a GLOBAL whitelist, not a diary-scoped rule** — any tag carrying `day="…"` will now split. Blast radius is nil today (zero occurrences across the 298 files, and the existing lookbehind stops `birthday="…"` false-splitting), but it is a global change, not a local one; `ui/ledger_viewer` needs a `transcript--diary` component class and CSS rule. **Note the palette is fully spent** — `$text-error` and `$text-primary` are the two sides, `$text-secondary` is `field-label`, `$text-muted` is `marker` and `thought`. A diary body should follow `thought`'s precedent (muted, and **not** side-tinted — a private reflection is not an act of allegiance) and distinguish itself on an SGR axis; the `day` attribute is an `attr` span.

### 2.9 Config and graph threading

- **Flag:** `GRAPHIA_PRIVATE_DIARIES`, `_env_flag(..., default=True)` — the default-on shape of `GRAPHIA_PRIVATE_THOUGHTS` and six siblings.
- **`config.py`:** `GraphiaConfig.private_diaries_enabled: bool = True`, defaulted so direct constructions in tests stay valid.
- **`graph.py:_assemble_graph`:** new keyword-only param, bound at **four** sites (corrected during Slice 2 — `day_diary`, `day_turn`, `collect_votes`, `mafia_point`; Slice 3's addition to the `day_diary` partial is `diary_store` / `game_id`, **not** this flag, so Slice 3 should not hunt a fifth) — but **split across slices, and binding them all at once is a hard `TypeError`** (corrected during implementation: `day_turn` / `collect_votes` / `mafia_point` do not accept the kwarg until Slice 2 adds their merged-block params). **Slice 1** binds the `day_diary` partial (`max_days`, `context_window`, `context_token_budget`, `recap_aware_reasoning_enabled`, `private_thoughts_enabled`) plus the edge rewire; **Slice 2** binds the three decision nodes; **Slice 3** adds `diary_store` and `game_id` to the `day_diary` partial.
- **Both builders:** `build_graph` passes `config.private_diaries_enabled`; `runtime/graph_builder.py:build_runtime_graph` gains the same keyword-only param with the anti-drift docstring paragraph the other eight flags carry.
- **`runtime/__main__.py`:** add it to the `build_runtime_graph(...)` call — **verified: that call site enumerates all thirteen threaded values explicitly, so omitting it silently drops the flag in remote mode**, the exact drift the module docstring's prior-incident note describes.

### 2.10 Measurement

**The arm label.** `settings.private_diaries_enabled` — a flat conditional boolean, emitted after `scripted_player` and before the nested `lineup`. Flat because there is one knob (spec 036's `settings.persona` sub-map earned its nesting with four); boolean because the direct analogue `persona.diversity_enabled` is one, while `scripted_player`'s strings name two real policies.

**Does spec 034's mislabel trap apply?** Structurally no — `persona_bench` passes the CLI value straight into `generate_personas(...)`, bypassing `load_config` entirely, and no such bypass exists here. **But two variants do, and one is already shipping:**

- **A permissive `getattr` default records the wrong arm silently.** Copying `scripted_player`'s `getattr(config, …, True)` shape means a config that cannot answer records the **on** arm for an **off** run, and unlike a `null` a `true` is indistinguishable from a measurement. Use a `None` sentinel and **fail fast before game 1** rather than after 30 minutes of tokens. This is not a graceful-degradation violation: that rule protects *instruments*; this is the *label saying which arm the run is*, and a record without it is worthless.
- **`settings.max_days` is a live defect of exactly this class.** `--max-days` sets its env var inside `_play_one_game`, *after* `main()` resolved the config that `run_eval` builds `settings` from — so a `--max-days 6` run records `12` today. Harmless so far (all 30 committed records are at the default) and **not this spec's to fix**, but it is the rule: **the diaries env assignment goes in `main()`, beside `--scripted-player`'s, before `load_config()`.**

**A CLI arm, not an env var around `make`.** `--diaries on|off`, following `--scripted-player`. The env route is **silently broken**: the `Makefile` does `include .env` then `export`, and a makefile assignment overrides the environment-derived value — reproduced experimentally (`FOO=from_shell make` yields `from_dotenv` when `.env` sets `FOO`). Today's `.env` holds no `GRAPHIA_*` gameplay flag so it works *by accident*, and flips the moment anyone adds one — producing a record that correctly says `true` while the operator believed they ran the off arm. Echo the arm in `main()`'s pre-run banner so it is visible before the batch starts.

**A third instance of the same trap:** `eval_transcript:_meta_get` is truthiness-gated, so a raw `False` in the header metadata would be silently dropped and the off arm's transcripts would carry no label. **Pass the string `"on"` / `"off"`.**

**No `METRICS_VERSION` bump.** The constant's own contract bumps for a changed detection rule or denominator, explicitly not for additive record-shape fields. No scorer reads `settings`; `tally_outcomes` is untouched. It **narrows** the comparability contract rather than invalidating it — expressed by the field itself, which is why the field exists. A bump would falsely flag all 30 committed rates as incomparable. Same precedent as `settings.persona.diversity_enabled`. A README sentence, not a version change.

**Absence is a third rendering case, and needs its own rule.** The ledger has `_STAND_IN_DEFAULT` (absent ⇒ the prior default) and `_KIND_DEFAULT` (absent ⇒ "a played game"). This is neither: a pre-039 record was played by a build with **no diary feature at all** — behaviourally the off arm insofar as ADR-011 parity holds, but not the claim "this run measured the off arm". **Render absence blank, never `false`**, which would assert a measurement never made. `evals/README.md` carries the explanation and the definition of a valid pair: two spec-039-era records on the **same commit**, same provider, same `run.kind`, same `metrics_version`, differing only in this field.

**Adjacent gap worth closing in the same change:** `settings.scripted_player` has been written to every record since spec 026 and is **never rendered in the drill-down** — it exists only as the table's `Stand-in` column. Exactly the defect the spec-036 follow-up fixed for `run.kind`, `generation` and `settings.persona`, left open for this one field. Add both lines conditionally.

**Running the six.** Measured pacing from committed `quality.duration_seconds`: Nova ≈2.0 min/game, Claude ≈2.9, ollama ≈6.4–7.0 — so four Bedrock runs ≈100 min sequentially and the ollama pair ≈130 min, giving ≈2¼ hours with ollama in parallel instead of ≈3¾.

Three hazards, in order of how likely they are to ruin a record:

| Hazard | Status | Handling |
| --- | --- | --- |
| **`code.dirty` cross-contamination** | **Live, and the real reason to isolate.** `collect_code_provenance` runs once at the top of `run_eval` and `git status --porcelain` sees untracked files — including a previous run's transcript dir. Three of four Bedrock arms would start inside the ollama window | Write transcripts **outside the repo** for the whole campaign, or run the ollama pair in a worktree |
| **Run-id collision** | Live. `make_run_id` is one-second resolution with no uniqueness check; `write_transcript` does `mkdir(exist_ok=True)` then `write_text` | Never launch two runs from one script inside the same second |
| **Concurrent ledger writes** | Structurally unguarded (no lock) but **not currently reproducible** — a document of the committed maximum size emits a single raw `write()`, atomic on an `O_APPEND` regular file. A CPython implementation detail, not a contract | Never point two live runs at the same `LEDGER_PATH`; merge by appending afterwards |

**Durable fix worth costing:** expose `--ledger-path` and `--transcripts-root` at the CLI. Both seams already exist (`append_record` resolves `LEDGER_PATH` at call time; `run_eval` takes `transcripts_root`), so it is two arguments, no metric surface, and it removes the worktree dance and the shim entirely.

**Transcripts: all six are keepers**, ≈2 MB / 60 files against 9.6 MB already tracked. These are not smoke runs — they *are* the recorded comparison, and the off-arm transcripts are the evidence the flag-off path wrote nothing. **Do not run `make clean-transcripts` during the campaign.** Sequencing matters more than volume: writing outside the repo and committing once at the end gives all six records one `code.commit` and `dirty: false`. Every `run.transcript_dir` link survives the move — the record stores only the directory name and `eval_ledger:transcript_dir_for` resolves it against the ledger's sibling directory.

**Validate the label cheaply first:** a 1-game ollama smoke run into a redirected `LEDGER_PATH`, confirming `settings.private_diaries_enabled` reads `false` on the off arm, **before** committing any Bedrock tokens. The ledger is append-only and repo-committed; a mislabelled record cannot be rewritten.

### 2.11 Helper-signature notes (recorded during Slice 2)

- **The helper defaults exist only for direct test calls.** `_private_record_block`'s `thoughts_enabled` / `diaries_enabled` are keyword-only with **no** defaults, deliberately, so a merged block never silently guesses a flag. The four *helpers* keep `diaries_enabled: bool = True` for the 019/024/025/028 direct-call convention — re-introducing that hazard one level up. Harmless because every live caller is bound by `_assemble_graph`, but **the node's `private_diaries_enabled` is the real guard**; the helper default is a test convenience, not a seam.
- **`_ai_diary`'s `diaries_enabled` is structurally dead** — `day_diary` returns `{}` on flag-off before the fan-out, so it can only ever be `True`. Threaded anyway rather than hard-coded, because hard-coding would reproduce spec 028's `_ai_reflect` defect one axis over. Belt-and-braces, not a live seam.
- **Naming inconsistency accepted knowingly:** §2.4 mandates `diaries` / `diaries_enabled` on the helpers while their siblings in the same signature are `private_thoughts` / `private_thoughts_enabled`, against §1's single-spelling rule. The short names are right *inside* `_private_record_block`; on the helpers `private_diaries` would have been consistent. Not worth churning.

---

## 3. Impact and Risk Analysis

- **`FakeLargeUnified` has no `Diary` queue — a hard prerequisite, and worse than 028's case.** `safe_llm` already patches `graphia.nodes.day.get_large`, so the binding is covered, but `with_structured_output(Diary)` raises `AssertionError("no scripted queue for schema …")` — and **the node's `try/except` swallows it into the fallback**, so a test would silently measure `_DIARY_FALLBACK` instead of failing. *Mitigation:* add the queue, the `diaries=` kwarg and the supported-schema message in the same change, plus a regression test that a flag-on full-game run produces a real scripted entry, not the fallback.

- **Privacy is the highest-stakes invariant**, as in 028: no `messages`, never `private_to`, keyed per player at every read. *Mitigation:* assert player A's Day-speak, vote and pointing prompts never contain player B's diary text.

- **Two flags, one slot** is genuinely new surface (§2.4). *Mitigation:* one test per cross-product cell.

- **Transcript renderer regression.** `_render_phases` is asserted on by spec 022 and 028 tests, and spec 038's highlighter reads its output. *Mitigation:* diaries-off must render byte-identically; test the off path against a pre-039 golden.

- **Edge-set change.** `day_close → night_open` becomes two edges. *Mitigation:* sweep `test_slice8_trace_tree.py`, `test_dual_mode_smoke.py`, `test_slice9_polish.py`.

- **Dual-mode byte-equal smoke.** Emits no public messages, draws no RNG, merge and window are pure ⇒ `#public-log` unaffected. Note the smoke scripts no `reflections=`, so 028's node **already** runs its deterministic fallback there and diaries will too — it exercises the fallback, not a real entry. Consider scripting `diaries=`.

- **Cost.** One extra `get_large()` per surviving AI per **Day** — roughly a fifth of 028's per-round volume, each output ~3× longer. Worst case (11 AI, 12-Day runaway) ≈120 extra calls, which the `max_days` guard removes.

- **Remote-mode super-step length.** The hinge now runs N model calls *and* N `GatewayMCPDiaryStore.write` calls, each spinning an event loop and an MCP session. Today's placeholder writes have the same shape at `night_close`, so the pattern is unchanged, but the day→night pause gets longer.

- **A near-certain null read as evidence of safety.** At an 8% base rate the off arm most likely reads 0–1 wins; a reader six months out sees "no harm" where the data says "we could not have seen harm." *Mitigation:* the bounded-claim sentence must appear in each of the six records' `notes`, not only in spec prose — prose does not travel with the data.

- **No new dependency, no new AWS resource, no IaC change.** The `DiaryStore` and its AgentCore Memory backing already exist and are already provisioned.

---

## 4. Testing Strategy

The diaries' *effect* is LLM-driven (effort-not-results, CR 005); the *structure* is fully testable with a fake LLM.

- **Write behaviour:** one entry per surviving AI before each Night; none for dead players, the human, or the eval stand-in; **none before Night 1**; **none on the winning Day**; none when the runaway cap is about to fire.
- **Reducer:** successive deltas concatenate in order; a new key does not disturb others; inputs are not mutated.
- **The interleave cursor** — the highest-value tests in the spec: a diary written after *k* thoughts renders after exactly those *k*; interleaving is exact across several Days; the merge is stable when thoughts are off (`k == 0` throughout); windowing out the oldest diary does not disturb the remaining cursors.
- **The window:** three or fewer ⇒ all shown; a fourth ⇒ oldest drops from the block **but is still in the transcript**.
- **Privacy:** no cross-player leak in any of the three prompts; no `messages`; no `private_to`.
- **The flag cross-product:** one test per cell of §2.4's table, including the byte-identity-by-delegation cases.
- **The bound:** the clamp holds for an over-long model entry; `DIARY_SENTENCE_BOUND > ` 028's stated prose bound.
- **Dual write:** the store receives the entry with `night_index == day + 1`; **a store exception does not prevent the state delta** and does not skip the remaining players.
- **Transcript:** a diary renders as `<diary player=… day=…>` in the day trailer, between the last `</round>` and `</day>`; defensive cases never raise; the off path is byte-identical to a pre-039 golden.
- **Measurement plumbing:** `settings.private_diaries_enabled` records **the invoked arm**, verified end-to-end on both arms; the `None` sentinel refuses to write an unlabelled record; absence renders blank, not `false`.
- **`safe_llm` / `FakeLargeUnified` coverage** for the new call site (required), with the swallowed-assertion regression test above.
- Structural invariants over textual equality throughout (architecture §6).
- Baseline to hold: **1561 passed, 1 skipped** in the main checkout.

---

## 5. Open decisions for the author

1. **Keep `night_close`'s read-back loop** as a liveness probe of the Gateway-fronted read path (recommended), or remove it with the placeholder write it was paired with.
-- use your judgement
2. **`--ledger-path` / `--transcripts-root` CLI arguments** — two arguments that remove the parallel-run worktree dance permanently. In this spec, or a separate tooling change?
-- here
3. **A `Diaries` column in the ledger table** beside `Stand-in`. Recommended, and safe (the right-justify split keys off `len(columns) - len(METRIC_ORDER)`, and `METRIC_ORDER` is untouched) — but the fixed-column list is the viewer's surface.
--here
