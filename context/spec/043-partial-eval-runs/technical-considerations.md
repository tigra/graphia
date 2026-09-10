# Technical Specification: Keeping What a Stopped Measurement Run Produced

- **Functional Specification:** [functional-spec.md](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

**The run's ledger record is written after its first completed game and rewritten in place after every game thereafter.** There is no signal handling, no graceful shutdown and no scratch file: durability comes from having already written, so a stop the process cannot observe — `SIGKILL`, a power cut, a reaper — leaves the last written state standing. That is the author's ruling, and it is the whole design in one sentence.

Three properties make it cheap rather than invasive:

**The scoring block is already total over "however many games got scored."** Everything at `blunder_eval.py:4413`–`4587` folds plain sums out of accumulators, gates every metric on `denominator > 0`, and lets `_attach_ci` (`:4564`) widen the Wilson bands honestly from a smaller denominator. So a partial record is *shaped* like every other record, and needs no second write path. What changes is **when** that block runs: today once, after the loop; under this spec once per completed game, and once more on the normal exit.

**The scoring block makes no network call.** `embed_fn` is resolved at `:4092`, before the loop, and used only inside it (`:4289`). So re-scoring per game is pure computation — no per-game Bedrock cost, and nothing that can fail for a credentials reason.

**Provenance is already sampled exactly once, before game 1** (`collect_code_provenance` at `:3946`). The record's `code.dirty` is therefore fixed before anything is written, and re-scoring never revisits it. The functional spec's clean-record requirement holds by construction rather than by care — the first write dirties the tree, and no later write re-reads it.

**Affected files:** `src/graphia/tools/blunder_eval.py` (the loop, the scoring block, the ledger writer), `src/graphia/eval_ledger.py` (two display sites), `evals/README.md` (the ledger contract).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The write: rewrite-in-place, and how it stays non-destructive

The ledger is a multi-document YAML file, one `---`-separated document per run, appended by `append_record` (`:3236`–`:3260`). This spec makes a run's own document mutable while the run is alive. Two facts make that safe to implement:

- **A run's document is always the last one in the file.** Nothing else appends during a run (concurrent runs are already told to use `--ledger-path` per arm, `:4584`), so the run can remember the file length *before* its first write and thereafter truncate to that offset and re-render its own document. Every earlier byte is preserved untouched, which is the same property a plain append has.
- **The byte-prefix invariant is checkable and must be asserted**, not assumed: after every rewrite, the file's first *N* bytes must equal what they were before the run's first write. This is the guard that turns "rewrite in place" from a risk into a bounded operation.

| step | when | what happens |
| --- | --- | --- |
| snapshot | before the first write | copy the whole ledger to a backup outside the tracked tree (see §2.2); remember its length in bytes |
| first write | after game 1 completes | append `---` + the rendered document, exactly as today |
| update | after each later game | truncate to the remembered length, append the freshly rendered document |
| final write | on normal exit from the loop | the same update, once more, from the same code path |

**The final write is not an optimisation to skip.** It is what keeps two existing behaviours intact: a run in which *every* game fails early still appends its (degenerate) record, which requirement five of the functional spec forbids disturbing and which no per-game write would ever produce; and a complete run's record must be byte-identical to today's.

**Concurrency stays out of bounds, and more sharply than before.** Two runs sharing one ledger file already race at `append_record`; under rewrite-in-place they would corrupt each other's documents rather than merely interleave. `--ledger-path` per arm is the existing answer and must be restated in `evals/README.md` as a requirement rather than a suggestion.

### 2.2 The pre-run backup

Before its first write, the run copies the ledger to a timestamped backup. **It goes outside the tracked tree** — under the project's local `.graphia/` footprint (architecture §3), not beside the ledger — for one reason: a backup inside `evals/` would be a new untracked file whose only effect is to dirty the tree, and the author's ruling is that tree-dirt should mean something actionable.

The backup is best-effort in one direction only: if it cannot be written, the run **must not** proceed to rewrite the ledger, because the backup is the only thing standing between a bug in the truncate-and-rewrite path and the committed history. Failing the run is the correct response; silently continuing is not.

### 2.3 The record: one gated marker, and the counts that already exist

**No new counts are needed.** The pair the functional spec asks for is already in the record:

| the fact | where it already lives | note |
| --- | --- | --- |
| games asked for | `settings.games` (`:2897`, from `args.games` at `:3960`) | **Must not be rewritten to the completed count.** `settings` is documented as the effective values so a run can be *repeated like-for-like*; the value that reproduces this run's intent is the number requested. Stamping the completed count there would collapse a 4-of-10 record onto a planned 4-game run and directly fail the functional spec's distinguishability criterion. |
| games finished | `quality.games_completed` (`:2977`) | one of the only three ungated `quality` fields |

**The one new field: `quality.ended_early: true`, gated (emitted only when true) and inserted first in the `quality` block.**

- **`quality`, not `run`.** `evals/README.md` frames that block as the run-health counts that stop "a degenerate run masquerading as a clean baseline" — this spec's requirement, written down before the spec existed. `games_failed_early` is the existing "this run fell short" signal and this is the same class of fact. Every run-health signal since (the spec-039 diary trio, the spec-040 story trio, the spec-041 substitution census) landed in `quality`; `run` is the identity block.
- **First in the block.** `_yaml_block` renders in insertion order, so insertion order *is* key order (`:2975`). The precedent for a conditional qualifier landing early rather than appended is `run.kind` (`:2803`–`:2810`), which sits immediately after `date` because it decides how everything below it reads. This field's whole job is to stop a reader parsing the three counts beneath it as a planned run.
- **Gated, and here absence is two-valued, not three.** Before this spec there is exactly **one** route to `append_record` (`:4588`, reached only by falling off the end of the loop at `:4411`), so every existing record is provably a run that attempted every game it was asked for. Verified across all 50 committed records: `games_attempted == games_completed + games_failed_early` holds in all 50, and `settings.games == games_attempted` in all 48 that carry both. **So a reader of an older record should conclude the run attempted everything it was asked to** — this is the `run.kind` treatment (absent ⇒ the normal case), not the `settings.private_diaries_enabled` treatment (absent ⇒ an unmeasurable third case, `:2913`–`:2922`), because there the off-arm claim was unmakeable of an older build and here the old code makes the reading for us.
- **A boolean, with no encoded cause.** `notes` is the documented human-mutable field for *why*, and it renders as `notes: ''` specifically to invite hand-editing. A machine-emitted cause string would repeat the `outcomes.note` mistake the README's own versioning section records.
- **Naming hazard to document in one sentence:** `ended_early` describes the **run**; `games_failed_early` describes a **game**. The `games_` prefix disambiguates, but it is the pair a reader will trip over.

**`METRICS_VERSION` stays at 1**, and the doctrine block (`:197`–`:235`) should say so out loud in the style of its existing spec-041 entry, or the next reader will assume a bump was forgotten. The version gates *rules and denominator definitions* (`:200`–`:201`); every scorer is byte-identical either side of this change, and a smaller *n* is what `--games` already varies. The fair counter-argument is selection — a run stopped *because* something was going wrong may not be a fair sample of what was planned — but that is a property of the sample, not the rules, and games run in index order, so a partial run's games are a **prefix** of the planned sequence rather than a subsample chosen after the fact. The instrument for the selection worry is the record's own marker, counts and hand-written `notes`.

### 2.4 The counter that must stay in balance

`result.games_attempted` increments at the **top** of each iteration (`:4163`), before the game runs; `games_completed` at `:4193` and `games_failed_early` at `:4185`. Because every write happens **after** a game reaches a terminal outcome, `games_attempted == games_completed + games_failed_early` holds at every write point with no code change. That identity is a cross-kind property of the ledger — `persona_bench.py:531` preserves it by construction too — and it is the second by-eye invariant a reader can check. **Nothing in this spec may break it**, and a test should assert it on a partial record rather than leave it to inspection.

### 2.5 The classification a reader can check

To be written into `evals/README.md` beside the existing `outcomes` partition invariant, in the same "if this does not hold, the record is suspect" register:

| `games_attempted` vs `settings.games` | `ended_early` | reading |
| --- | --- | --- |
| equal | absent | full run; failures, if any, are in `games_failed_early` |
| less | `true` | ended after `games_completed` games |
| less | absent | **should not exist — the record is suspect** |

### 2.6 The viewer: two lines, and why they are in scope despite §3

The functional spec puts *presenting* incomplete runs out of scope. These two changes are not presentation; they are an ambiguity this spec introduces into an existing display, and the spec that creates it owes the fix.

- **`eval_ledger.py:3016`** renders the `Games` column from `_resolved_games` (`:319`–`:325`), which reads **`settings.games`** — the number *requested*. So `make view-ledger` shows a run stopped after 4 of 10 as `Games: 10`, in a row whose metric cells a reader then reads as a ten-game measurement. That cell is not wrong today (all 50 records have `settings.games == games_attempted`); **this spec is what makes it wrong.** Render `4/10` when the record carries the marker, `10` otherwise.
- **`_render_quality_section` (`:3728`–`:3753`)** renders `quality` by an explicit **whitelist**. Without one line added there, the marker is invisible in the drill-down as well as the table — the record would carry it and the viewer would show it nowhere.

Leave the `⚠` indicator alone: it currently means `code.dirty`, and overloading it would conflate two distinct caveats.

### 2.7 No ablation flag

ADR 011 requires a default-on `GRAPHIA_<FEATURE>` flag for every *gameplay-influencing* change and exempts non-gameplay ones. This changes no game behaviour — it is the measurement layer's write path — so it is exempt, on the same footing as the spec-020 clock and spec-021 transcript labels. Stated here because it is a recurring review question (spec 040 §2.12 had to argue it).

### 2.8 Cost of re-scoring, and the one number to measure

Re-running `:4413`–`4587` per game is *N* times the scoring work instead of once. Most of it is trivial folding, but **`score_repetition` is pairwise near-duplicate detection over all pooled AI lines**, so its cost grows with the square of accumulated lines and is paid again at every game. Record 47 pooled 902 lines over ten games; the total re-scoring work across a run is therefore on the order of the sum of squares rather than one square. This is expected to be seconds, not minutes, and is dwarfed by a 33-minute local game — but it is the one place where "just call the existing block again" could surprise, so it must be **measured on a real ten-game run and recorded**, not assumed. If it does bite, the mitigation is to re-score on a cadence (every game up to some *N*, then every *k*th) with the final write always exact.

---

## 3. Impact and Risk Analysis

**System Dependencies**

- `blunder_eval.render_record` / `append_record` — the record shape and the write. Top-level key order run→code→provider→settings→quality→outcomes→vote_activity→generation→metrics→notes is pinned by `tests/test_blunder_eval.py:569`–`581`; a gated `quality` sub-key does not disturb it.
- `eval_ledger.py` — read-only consumer. Its parser is `yaml.safe_load_all` into plain dicts with defensive reads, so an unknown `quality` key breaks nothing; the two display sites in §2.6 are the deliberate changes.
- `collect_code_provenance` (`:3946`) — unchanged, and its once-only call site is load-bearing for the clean-record requirement.
- `evals/README.md` — the ledger contract; three edits (the `quality` field list, the §2.5 invariant table, the per-arm `--ledger-path` requirement).

**Potential Risks & Mitigations**

| risk | mitigation |
| --- | --- |
| **A bug in truncate-and-rewrite damages committed history.** This is the one risk that is not recoverable by re-running. | The pre-run backup outside the tracked tree (§2.2), a hard failure if it cannot be written, and a byte-prefix assertion after every rewrite (§2.1). |
| **A process killed *during* a rewrite leaves a truncated final document.** The window is small but it is exactly when kills happen. | Write to a temporary file in the same directory and `os.replace` it into position — atomic on POSIX — so a kill leaves either the previous complete state or the new one, never a half-written document. The earlier records are re-written from bytes already read, so no parse-and-re-render round trip can reformat them. |
| **Partial records get pooled with full baselines by eye.** | The gated marker first in `quality`, the §2.5 invariant, and the `4/10` viewer cell (§2.6). |
| **Partial runs make untracked transcript dirs common.** A partial run leaves an incomplete `evals/transcripts/<run-id>/` linked from `run.transcript_dir`; that directory is what makes the *next* run stamp `dirty: true`. The mechanism does not create the hazard — a full run leaves the same dir — but partial runs will now be frequent where before they left nothing. | Per the author's ruling this is meaningful and stays: the remedy is to commit or remove. Document it in `evals/README.md` beside the existing commit-or-delete norm. |
| **Re-scoring cost grows quadratically in pooled lines.** | Measure on a real ten-game run (§2.8); cadence fallback if needed. |
| **Two concurrent runs on one ledger corrupt each other.** | Already the case for appends; restate `--ledger-path` per arm as a requirement in `evals/README.md`, and consider refusing to start when the ledger's tail document belongs to a run that is still alive. |

---

## 4. Testing Strategy

The suite is fully mocked and must never touch the committed ledger; every existing test either passes `ledger_path=tmp_path/…` to `append_record` or monkeypatches `blunder_eval.LEDGER_PATH` (`test_blunder_eval.py:1941`, inside `_stub_run_eval_env` at `:1913`). Both keep working because `append_record` resolves `LEDGER_PATH` at call time (`:3253`–`:3254`).

**The scaffold already exists and is the right one.** `_stub_run_eval_env` drives `run_eval` in-process with a stubbed `_play_one_game`; a partial run is expressed by making that stub raise or by stopping after *k* calls. **No test should send a real signal** — there is no signal handling in this design, which is what makes these tests trivial.

Cases that must be covered, each mapping to an acceptance criterion:

- **Progressive shape.** A four-game run: after game 1 the ledger holds one document covering one game; after game 4 it holds **one** document covering four. Asserts the "one entry per run, never one per game" criterion, which is the one a naive per-game append would fail.
- **Byte-identical complete run.** A run that plays every game renders a record byte-identical to today's. This is the regression that protects every existing reader and every committed comparison.
- **Non-destructive rewrite.** With two pre-existing documents in the ledger, a partial run's rewrites leave the first two documents byte-identical throughout — asserted on bytes, not on parsed values, because a parse-and-re-render round trip is exactly the failure this guards.
- **Provenance is not re-sampled.** `collect_code_provenance` is monkeypatched to a counting stub; a four-game run calls it **once**. This is the mechanical form of the clean-record criterion, and stronger than asserting the flag's value.
- **The marker and the invariant.** A partial record carries `ended_early: true` as the first `quality` key; a complete one omits it entirely; `games_attempted == games_completed + games_failed_early` holds in both.
- **The all-failed full run is undisturbed.** Ten games, every one raising: one record, `games_completed: 0`, **no** `ended_early` — it attempted everything it was asked for. This is requirement five, and it is the case a `games_completed == 0` guard would silently break.
- **Nothing finished, nothing written.** A run killed before game 1 completes leaves the ledger byte-identical.
- **Backup preconditions.** The backup is written before the first ledger write; an unwritable backup location fails the run before the ledger is touched.
- **Viewer.** `_resolved_games` / the `Games` cell renders `4/10` for a marked record and `10` otherwise; `_render_quality_section` shows the marker. Both are pure-function tests over hand-built records.

**Not covered by the suite, and stated rather than implied:** that the record survives a real `SIGKILL` or power loss. That is a property of having already written to disk, and the honest verification is one manual kill of a real run — which the mocked suite cannot express and should not pretend to.
