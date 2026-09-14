# Technical Specification: Keeping What a Stopped Measurement Run Produced

- **Functional Specification:** [functional-spec.md](./functional-spec.md)
- **Architecture Decision:** [ADR 014 — Optimistic-Concurrency Whole-File Ledger Writes, over Strict Append-Only](../../adr/014-optimistic-concurrency-ledger-writes.md) (Accepted 2026-09-11)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

**The run's ledger record is written after its first completed game and updated after every game thereafter, each update being a whole-file atomic replace guarded by optimistic concurrency control.** There is no signal handling, no graceful shutdown and no scratch file: durability comes from having already written, so a stop the process cannot observe — `SIGKILL`, a power cut, a reaper — leaves the last written state standing.

**Concurrent runs stay safe, and end up safer than they are today.** No lock is taken — OCC with retries instead, because a lock file is one more artifact a killed run can leave behind, and kills are this spec's whole subject. Each write hashes the ledger it read, splices its own document in as text, and replaces the whole file atomically only if the hash still matches; otherwise it retries. Today's `append_record` does two unsynced appends and can interleave between concurrent writers, so this **removes** an existing corruption window rather than opening one.

Three properties make it cheap rather than invasive:

**The scoring block is already total over "however many games got scored."** Everything at `blunder_eval.py:4413`–`4587` folds plain sums out of accumulators, gates every metric on `denominator > 0`, and lets `_attach_ci` (`:4564`) widen the Wilson bands honestly from a smaller denominator. So a partial record is *shaped* like every other record, and needs no second write path. What changes is **when** that block runs: today once, after the loop; under this spec once per completed game, and once more on the normal exit.

**The scoring block makes no network call.** `embed_fn` is resolved at `:4092`, before the loop, and used only inside it (`:4289`). So re-scoring per game is pure computation — no per-game Bedrock cost, and nothing that can fail for a credentials reason.

**Provenance is already sampled exactly once, before game 1** (`collect_code_provenance` at `:3946`). The record's `code.dirty` is therefore fixed before anything is written, and re-scoring never revisits it. The functional spec's clean-record requirement holds by construction rather than by care — the first write dirties the tree, and no later write re-reads it.

**Affected files:** `src/graphia/tools/blunder_eval.py` (the loop, the scoring block, the ledger writer), `src/graphia/eval_ledger.py` (two display sites), `evals/README.md` (the ledger contract).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The write: optimistic concurrency control over a whole-file atomic replace

**The mechanism the author chose is OCC with retries — no lock.** That is deliberate and it is the right trade for this harness: a lock file is one more thing that can be left behind by a killed run, and kills are the whole subject of this spec. OCC has nothing to leave behind.

**The existing write is the thing being replaced, and it is already unsafe.** `append_record` (`:3257`–`:3259`) opens the ledger in append mode and performs **two** writes (`"---\n"`, then the document) with **no `fsync`**. A kill between them leaves a separator with no document; a kill mid-document leaves a truncated one; a power cut can leave unflushed bytes. And a multi-kilobyte buffered write is far above `PIPE_BUF`, so two runs appending to one ledger can interleave. **Concurrent runs on one ledger are not safe today** — records 47 and 48 survived only because they finished four minutes apart. This spec therefore *reduces* the corruption surface rather than enlarging it, and that framing belongs in the ADR.

**The write cycle.** All of it operates on the file as **text**, split on the document separator into opaque chunks. No other run's document is ever parsed or re-emitted, so hand-written `notes`, float renderings and key order survive byte-exactly — a parse-and-re-render round trip would reformat all fifty committed records on every write.

| step | operation | why it is there |
| --- | --- | --- |
| 1 | render this run's document from the in-memory result | the expensive part (scoring), done **before** the read so the read-to-replace window stays small |
| 2 | read the ledger bytes; `sha256` them | the content hash is the OCC version token — exact, unlike mtime granularity or size |
| 3 | split into chunks; replace this run's chunk by its id, or append it if absent | other chunks pass through untouched |
| 4 | re-read and re-hash; if it differs from step 2, **conflict** → retry from 1 | cheap pre-check that avoids most wasted writes |
| 5 | write to a temp file in the same directory, `fsync`, `os.replace`, `fsync` the directory | atomic: a reader or a kill sees the old complete file or the new one, never a partial |
| 6 | read back; confirm exactly one chunk carries this run's id and it is what was written | closes the loop — this is what makes "with retries" converge |
| 7 | on exhausted retries: **do not fail the run** | a failed ledger update must never kill a measured run; that would invert the spec's purpose |

**The convergence argument, and the residual window — stated rather than glossed.** Two writers can both pass step 4 and then replace in sequence; the second one's file carries the first one's chunk as it was at *its* read, so the first writer's newest update is rolled back one generation. Step 6 does not catch this, because the victim's own chunk is intact — it is the *other* chunk that regressed. What repairs it is the victim's **next** write, which re-reads and re-writes its current state. So progressive updates self-heal, and the exposure is bounded to "a record is briefly one game stale".

**The final write is the one that cannot self-heal**, because there is no next write to repair it. Three things protect it: the read-to-replace window is microseconds by construction (step 1 precedes step 2); the final write retries harder than a progressive one; and if it still cannot land, the run writes its rendered document to a clearly-named fallback file beside the ledger and says so loudly, so the measurement is never lost even if the ledger cannot be updated. The residual risk is a record that is stale by a game — visible, because `games_completed` would disagree with the transcript count on disk.

**If conflicts ever show up in practice, the escalation is `flock` on a gitignored sidecar** — which would cost one ignored file and close the window completely. Recorded as the known next step, not built now: with progressive writes every ~4 minutes on the cloud arms and ~33 minutes on the local one, the collision probability is very small and OCC's cheapness in the common case is the point.

**Concurrency is now supported rather than forbidden.** This replaces the previous draft's instruction to use `--ledger-path` per arm. That flag remains useful for throwaway runs, but it is no longer a safety requirement — which matters, because running the arms concurrently is exactly what a multi-arm baseline session does.

### 2.2 Run identity: a collision-proof id on every record

The write cycle needs each run to recognise its own document, and `_run_id` (`:3280`) cannot support that: it is a local timestamp to the **second**, so two runs launched in the same second produce the same id. That is not hypothetical — this project has launched pairs four seconds apart.

- **`run.id`, always present, on every record from now on.** The existing sortable timestamp plus a short random suffix (e.g. `2026-09-10T13-31-38-a7f3`): still lexically chronological, still human-readable, and collision-proof in practice.
- **It stays the same string as the transcript directory name**, so the cross-reference a reader makes by eye keeps working, and `run.transcript_dir` continues to name a real directory.
- **Verify at implementation** whether anything parses that directory name as a bare timestamp before appending the suffix — `eval_ledger.py`'s transcript browser resolves run directories, and a strict `strptime` there would reject the new form. Existing directories keep their names; only new runs gain the suffix.
- Older records carry no `run.id`. Nothing needs it retroactively: the field exists for a run to find its own document while it is alive, and every older record belongs to a run that ended long ago.
- **Optional, and recommended for diagnosability:** a per-run write counter (`run.seq`) incremented on each update. It makes "which write won a conflict" answerable after the fact, which is otherwise guesswork. Additive and gated like the marker.

### 2.2b The pre-run backup

Before its first write, the run copies the ledger to a timestamped backup under the project's local `.graphia/` footprint (architecture §3) — **outside the tracked tree**, so it cannot dirty it. Under OCC the backup is belt-and-braces rather than the only line of defence, since step 5 never leaves a partial file; its job is to survive a *logic* bug in the chunk-splicing, which atomicity cannot protect against. If the backup cannot be written, the run must not proceed to write the ledger.

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

Re-running `:4413`–`4587` per game is *N* times the scoring work instead of once. Most of it is trivial folding, but **`score_repetition` is pairwise near-duplicate detection over all pooled AI lines**, so its cost grows with the square of accumulated lines and is paid again at every game. Record 47 pooled 902 lines over ten games; the total re-scoring work across a run is therefore on the order of the sum of squares rather than one square. The draft's prediction is kept here as written, because a prediction and its outcome are worth more together than the outcome alone:

> This is expected to be seconds, not minutes, and is dwarfed by a 33-minute local game — but it is the one place where "just call the existing block again" could surprise, so it must be **measured on a real ten-game run and recorded**, not assumed.

**Measured 2026-09-14: the prediction is wrong on the Nova arm, by an order of magnitude — and its own closing clause is what caught it.** Ten progressive passes over record 47's pool cost **363.5 s** on top of the **93.9 s** that a single final pass costs: minutes, not seconds, and **18.1%** added to a run that took 2003.9 s. The *model* was not the error — the sum-of-squares prediction is confirmed below to within 1%. The **yardstick** was the error. It compared against a 33-minute *local* game, when the arm that pools 902 lines finishes a game in about 200 s. What governs the share is **pooled lines per unit of wall clock**, not wall clock; so the prediction is *correct for the local arm and wrong for Nova*, and this section cannot carry one verdict for all three arms. The instrumented ten-game run this section went on to demand has since been done — **it confirms the model and disagrees with the replay about the run**, and that disagreement is a finding in its own right rather than a discrepancy to average away.

**First route — a replay of record 47's own pool, measured first because Bedrock was unreachable.** The ten-game Bedrock run this section demands could not be run at the time — the SSO session had expired `2026-09-12T18:26:39Z` and there was no `AWS_BEARER_TOKEN_BEDROCK` fallback, so no Bedrock call could be made at all; it has since been run, and is recorded below. Rather than assume, the quantity was measured a different way, on real data from the exact run this section already cites. Record 47 (`evals/blunder-ledger.yaml`, 2026-09-10, `provider: bedrock`, Nova Pro/Lite, ten games, `run.duration_seconds: 2003.911`, `metrics.repetition.denominator: 902`) has all ten of its game transcripts committed at `evals/transcripts/2026-09-10T13-31-38/`. Its pooled AI Day-speech lines were reconstructed from them — lines of the form `Name: text` inside `<day>`, excluding `Moderator:`, excluding the `human="true"` scripted seat, and **excluding the contents of `<vote>` blocks** (ballot answers are not Day speech; including them over-counts by exactly `substitutions.ballot.attempted`). `score_repetition` was then called on each of the ten cumulative prefixes the progressive write actually hits, with `ai_names` accumulated as a growing union exactly as `run_eval` does at `:6506`.

**The replay is admissible because its fidelity is proven, not assumed.** It reproduces record 47's committed metrics **bit-for-bit** on every field of both pooled-line metrics:

| field | replay | record 47 as committed |
| --- | --- | --- |
| `repetition.rate` | 0.7339246119733924 | 0.7339246119733924 |
| `repetition.count` | 662 | 662 |
| `repetition.denominator` | 902 | 902 |
| `third_person_self_talk.rate` | 0.025498891352549888 | 0.025498891352549888 |
| `third_person_self_talk.count` | 23 | 23 |
| `third_person_self_talk.denominator` | 902 | 902 |

So the pool re-scored is *the* pool that run scored, not a synthetic one of the same size. Timed on the project host (Darwin 25.3.0, Python 3.14.5, CPU otherwise idle), one sample per prefix.

**What each of the ten progressive passes costs.**

| after game | game's lines | pooled lines | `score_repetition` s | `score_third_person_self_talk` s |
| --- | --- | --- | --- | --- |
| 1 | 111 | 111 | 1.367 | 0.00023 |
| 2 | 70 | 181 | 3.625 | 0.00030 |
| 3 | 85 | 266 | 7.730 | 0.00043 |
| 4 | 103 | 369 | 15.402 | 0.00058 |
| 5 | 76 | 445 | 23.031 | 0.00071 |
| 6 | 110 | 555 | 35.239 | 0.00085 |
| 7 | 100 | 655 | 49.053 | 0.00099 |
| 8 | 62 | 717 | 58.189 | 0.00108 |
| 9 | 95 | 812 | 75.938 | 0.00124 |
| 10 | 90 | 902 | 93.942 | 0.00144 |

**The totals.**

- **pre-043** — one pass at the full 902-line pool: **93.944 s**
- **spec 043** — ten progressive passes: **363.524 s**, plus the final exact pass **93.944 s** → **457.467 s** (7.6 minutes)
- **added by spec 043: 363.524 s — 3.87× the old cost**

Two framings of the share, and both belong here because they answer different questions:

- **Against what record 47 actually took** (2003.911 s): the spec-043 finalise total is **22.83%** of the run, the added cost **18.14%**. This answers "how big is this beside a run I have already seen?"
- **Against what the same run would have taken under spec 043** — 2003.911 − 93.944 + 457.467 = **2367.4 s**: the finalise total is **19.3%** of the run, and the run is **18.1% longer** than it was. This answers "what will I actually wait?"

**The sum-of-squares model is confirmed rather than asserted, which is what makes these figures extrapolate.** Predicted from the line counts alone, Σ(n_k/n₁₀)² + 1 = **4.90×** the single-pass cost; measured **4.87×**. The per-prefix ratios of measured to pure quadratic sit between **0.946** and **1.007** across the whole range, so the cost really is quadratic in pooled lines and the run total really is the sum of squares — a longer run's cost can be predicted from its line counts without measuring it. The quadratic is exact rather than a worst case because `_clusters` (`src/graphia/tools/repetition_experiment.py`) is union-find over **all** C(n,2) pairs with no early termination: 406,351 `SequenceMatcher.ratio()` calls at n = 902.

**§3 Impact and Risk Analysis was right about the three cost classes, and the whole cost is the one superlinear term.** `score_third_person_self_talk` — the linear pooled-line pass — costs **1.44 ms** at 902 lines, four orders of magnitude below the pairwise term. The scalar folds and the Wilson bands re-derived from them do not register at all.

**The cost is arm-dependent, and the conclusion differs per arm.** What drives the share is pooled lines per unit of wall clock, and the three arms sit far apart on it:

| arm | pooled AI lines, ten games | wall clock | finalise total | share of run |
| --- | --- | --- | --- | --- |
| Nova (record 47, **measured**) | 902 | 2003.9 s | **457.5 s** | **19–23%** |
| `bedrock-claude` (record 48, extrapolated) | 300 | 2277.0 s | ≈50 s (a full pass costs (300/902)² × 93.944 ≈ 10 s) | ≈2% |
| Ollama local (records 45 and 46, one game each: 92 and 97 lines in 1670.461 s and 1994.643 s; extrapolated) | ≈950 | ≈18,000 s | ≈460 s | ≈2.5% |

**Nova is the outlier because its games are talkative** — 902 AI lines over ten games against `bedrock-claude`'s 300, which is the same finding the backlog already records about cost. The local arm pools about as many lines as Nova and pays about the same seconds for them, but against a wall clock roughly **nine times longer**; that is precisely why "dwarfed by a 33-minute local game" was a true statement that did not transfer.

**There is no cheap caching fix, stated here because the next reader will reach for one first.** `score_repetition` recomputes `masked = [_spec009_normalize(_spec009_mask_names(line, ai_names)) …]` (`:1243`) over the **whole** pool on every pass, and `ai_names` is the union of AI player names across games, which **grows every game** — measured on record 47: 7 names after game 1, 45 after game 10. A name dealt in game 7 masks that name in game 1's lines too, so an old line's masked text genuinely changes when the pool grows. Re-masking and re-clustering the whole pool from scratch is therefore **required for exactness**, not laziness: the obvious "cache the previous pass and cluster only the new lines" is not an exact computation and is not available.

**The instrumented ten-game Nova run, 2026-09-14: the two routes agree about the model and disagree about the run.** Run `2026-09-14T15-03-46-6f2b94` is **record 52** — ten `bedrock` Nova Pro/Lite games, `run.duration_seconds: 2969.058`, **808** pooled AI lines (a less talkative run than record 47's 902), `repetition` 574/808 for a rate of 0.7103960396039604 against record 47's 0.7339, complete with `ended_early` absent. It was deliberately executed on **pre-mitigation** code so that it measures the cost this section is about, and is committed at `fd1b84e` with its ten transcripts. The instrument (already committed at `1eb5839`, run unmodified) reported:

```
finalise-cost TOTAL: passes=11 writes=11 finalise_s=1189.729 write_s=0.698 run_s=2969.058 finalise_pct=40.071 write_pct=0.024 pool_lines=808
```

**The eleven-pass cadence is confirmed in situ**: ten progressive passes plus one final exact pass, exactly as designed.

**What each pass cost, beside what the replay's model says it should have.** The replay's fitted cost — `cost(n) = 93.944 × (n/902)²` — predicts every pass of this run from its pool size alone, so the `measured/model` column is the whole story.

| pass | after game | final | pooled lines | measured s | model s | measured/model |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | no | 63 | 0.419 | 0.458 | 0.914 |
| 2 | 2 | no | 143 | 2.386 | 2.361 | 1.011 |
| 3 | 3 | no | 247 | 6.774 | 7.044 | 0.962 |
| 4 | 4 | no | 352 | 13.719 | 14.307 | 0.959 |
| 5 | 5 | no | 411 | 18.845 | 19.505 | 0.966 |
| 6 | 6 | no | 494 | 25.905 | 28.178 | 0.919 |
| 7 | 7 | no | 560 | 114.826 | 36.210 | **3.171** |
| 8 | 8 | no | 651 | 141.331 | 48.935 | 2.888 |
| 9 | 9 | no | 721 | 181.708 | 60.024 | 3.027 |
| 10 | 10 | no | 808 | 236.460 | 75.384 | 3.137 |
| 11 | 10 | **yes** | 808 | **447.355** | 75.384 | 5.934 |

**Passes 1–6 track the model to within 9%, and then the run stops obeying it.** Pass 7 jumps to **3.171×** while the pool grows only **1.13×** — 494 → 560 lines, which the quadratic prices at 1.29× against a measured step of 4.43×, so no quadratic can produce it — and passes 8–10 hold at 2.888, 3.027, 3.137. The cleanest evidence is internal to the run: **pass 11 cost 447.355 s on the byte-identical 808-line pool that pass 10 had just scored in 236.460 s**, 1.89× for the identical computation minutes apart. In-run cost is therefore **not a function of pool size alone**, and **40.07% must not be reported as the design's intrinsic cost**.

**Diagnosed rather than guessed — and one hypothesis was killed by measurement.** Re-clustering record 52's own final pool on a quiet machine takes **75.030 s against the model's 75.384 s — 0.995×**, on a pool rebuilt from the record's committed transcripts to exactly 808 lines (= its `metrics.repetition.denominator`) and reproducing its committed `metrics.repetition.count` of 574. **So the model is right and the data is not the cause**; the in-run pass was **3.15× slower than the identical computation on identical data**. The obvious next suspect was heap growth and GC pressure inside a long-lived process — `SequenceMatcher` allocates heavily and the harness accumulates ten games of transcripts, messages and diaries — and it is **refuted**: the same clustering run twice in one process costs 75.030 s, then 74.680 s. The refutation is recorded rather than dropped, because it is the first thing the next reader will also guess. Two further candidates are excluded the same way: `pmset -g log` shows **no sleep and no `DarkWake`** anywhere in the run's 49-minute window, and `pmset -g therm` reports **no thermal or performance warning level ever recorded**. The job's `caffeinate -dimsu` assertion (pid 38176) held for 00:49:31 and died exactly when the run ended, so the backlog's sleep hazard did not fire; this is a different failure.

**The surviving explanation is memory pressure, and the asymmetry is what points at it.** `vm.swapusage` reports **20.5 GB of 21.5 GB committed** — a legacy of the `qwen3-coder:30b` model that had been resident at 21.7 GB VRAM until roughly 15:06, minutes after this run began at 15:03:46. And **the games themselves stayed flat while only the finalise slowed**: per-game wall clock, derived by subtracting each pass's cost from the interval between ledger writes, runs 141.6, 191.6, 213.2, 209.3, 122.2, 160.1, 167.2, 198.7, 177.3, 201.5 s — all in line with record 47's ~200 s per game. **Memory pressure degrades allocation-heavy CPU work and leaves network-bound gameplay alone, which is exactly the asymmetry observed.** Stated as the leading diagnosis, not as a certainty: it does not account for the *onset*, since the pass-7 boundary falls roughly 21 minutes into the run (≈15:25), some nineteen minutes after the local model unloaded — so co-residency with that model is not the mechanism, and a system that never reclaimed after it would have to be. What would settle it is the same ten-game run on a machine that has not just hosted a 30B local model.

**So this section carries two figures, and neither is the other's error bar.**

- **Intrinsic — what the design costs when the model holds: ≈17.1%.** Summing the model over this run's *actual* pass sequence gives **367.791 s**; the run would then have taken 2969.058 − 1189.729 + 367.791 ≈ **2147 s**, of which the finalise total is **17.1%**. This figure is **derived, not measured** — it is the replay's model applied to the real pass sequence — and it is the one to design against. It also lands beside the replay's own share for record 47 (19.3% of the run spec 043 would have turned it into, and 18.1% added to the run it actually took) on a pool 10% smaller, so the two routes agree to within a couple of points. That agreement is the thing this section said was worth having, and it is what licenses the extrapolations in the arm table above.
- **Observed — what it actually paid: 40.07%.** This is not an artefact to be waved away. It is what a real measured run cost on a real developer machine immediately after a local-model run, and this project's own workflow makes that sequence normal, since the three arms are run against each other for comparable records. It is the figure to budget wall clock against.

**The arm-dependence conclusion above survives it unchanged.** What governs the share is pooled lines per unit of *gameplay* wall clock, and record 52 sits essentially where record 47 does: 808 lines over 1779.3 s of gameplay is 0.454 lines/s against record 47's 0.472. Nova is still the outlier arm, for the reason the table gives.

**The write path's cost question is closed: `write_s=0.698` over eleven writes — 63 ms each, 0.024% of the run.** This section had no measured figure for the write at all until now, only the argument in §2.1 (The write: optimistic concurrency control over a whole-file atomic replace) that a whole-file replace is cheap at this file's size. It is noise — and it is noise **even under the memory pressure that tripled the re-scoring**, which is a stronger statement than a quiet-machine measurement could have made. The local arm's record 51 agrees from the other end of the scale: 0.102 s over four writes, 25 ms each.

**One caveat on record 52, stated so the record is not over-read.** Its `code.dirty` is **true**, set by two uncommitted `context/project-timeline.*` documentation files — the reason is written into the record's own `notes`, so the record explains itself. The *measurement* is unaffected: no gameplay or scoring code differed from `68c43b2`. But the record is not **provenance**-comparable to record 47, which is clean, and a reader comparing the two rates (0.7104 against 0.7339) should weigh that alongside the smaller pool.

**What remains outstanding is no longer the run but a repeat of it.** The instrumented measurement Task 7.1 asks for exists, and it also closes the replay's one exposure: the replay timed the scorers in isolation, whereas `finalise-cost` times the whole of `_finalise_result` from inside it, and the two agree to within 9% across passes 1–6 — on a *different* run's pool from the one the model was fitted to — so nothing else in the finalise path is material. What is still unmeasured is the same ten-game Nova run on a machine **not** under memory pressure. That is what would turn the 3.15× degradation from a leading diagnosis into a settled one, and would confirm the 17.1% intrinsic figure by measurement rather than by model.

**DECIDED 2026-09-14 (Task 7.2): it bit — and the cadence this section named was rejected anyway, so what shipped is a deliberate deviation from this section's own fallback.** The task's first branch ("record that the cadence is not needed, so nobody implements it defensively") is closed off by measurement: 40.07% observed and ≈17.1% intrinsic are minutes, not the seconds predicted at the top of this section. The second branch — implement the cadence — was rejected too, on three grounds the figures above supply directly. **(i) A cadence trades away the feature this spec exists to deliver.** Re-scoring every *k*th game leaves the live record stale for up to *k*−1 completed games, which is precisely the loss spec 043 prevents; Task 7.3's 4 Hz poller watched the marker and the counts track each completed game *exactly* on a real three-game run, and a cadence gives that up. **(ii) It cannot touch the worst case, by construction.** The task requires the final write to be exact, and in record 52 the final pass was the single most expensive pass of the entire run at **447.355 s**. A cadence reduces how many passes are paid for; it can never reduce the one it is obliged to run. **(iii) There was a provably exact alternative that removes ~95% of the cost and keeps every progressive write exact.** So the honest reading of Task 7.1's number is not "re-score less often" but "the pass was needlessly expensive all along" — and a cadence would have spent this spec's budget hiding that rather than fixing it.

**What shipped instead: an upper-bound skip inside `_clusters`.** `_clusters` (`src/graphia/tools/repetition_experiment.py`) was union-find over **all** C(n,2) pairs with no early termination — the 406,351 `SequenceMatcher.ratio()` calls at n = 902 this section already cites as the reason the quadratic is exact rather than a worst case. `difflib` documents `real_quick_ratio() >= quick_ratio() >= ratio()`, so a pair whose upper bound already sits below the threshold **cannot** reach it and skipping it changes no verdict. Only **2.42%** of record 47's pairs (9,841 of 406,351) survive both bounds. The `b2j` index is also reused across the inner loop, which is worth almost nothing on its own (91.5 s → 87.2 s) and is kept only because the orientation constraint below makes that loop order mandatory regardless; **the skip is the entire win.**

**Measured before and after, on the two real pools this section is built on.** Same host, same quiet-machine conditions as the replay above, `score_repetition` timed end to end (name-masking included) so the figures are comparable to the replay's table.

| pool | pre-skip | post-skip | factor |
| --- | --- | --- | --- |
| record 47, 902 lines — one pass | 93.944 s | **3.981 s** | 23.6× |
| record 47 — the full eleven-pass sequence | 457.467 s | **19.727 s** | 23.2× |
| record 52, 808 lines — one pass | 75.030 s (measured) / 75.384 s (model) | **4.292 s** | 17.5× |
| record 52 — its actual eleven-pass sequence | 367.791 s (the intrinsic figure) | **20.871 s** | 17.6× |
| the whole committed transcript corpus, 20 rebuildable pools | several minutes | **21.3 s** | — |

**The number that settles it: record 52's intrinsic share falls from 17.1% to 1.16%.** Summing the *measured* post-skip cost over that run's actual pass sequence (pool sizes 63, 143, 247, 352, 411, 494, 560, 651, 721, 808, 808 — reconstructed from its committed transcripts, and reproducing the instrument's in-situ pass log line for line) gives **20.871 s**. The run would then have taken 2969.058 − 1189.729 + 20.871 = **1800.2 s**, of which the finalise total is **1.16%**, and the part spec 043 *added* is 16.6 s. Record 47 lands in the same place from the other route: **1.02%** of the 1929.7 s the run would have become, 15.8 s added, **0.8%**. **So the cadence is now definitively unnecessary** — and it is unnecessary with room to spare, because even if the whole 3.15× in-situ degradation diagnosed above were to recur, 20.871 s becomes ≈65.7 s and the share ≈**3.6%**, still an order of magnitude under what the cadence was being considered for. The prediction this section opens with — *"seconds, not minutes"* — is true after the change, on the Nova arm it was wrong about: 20.9 seconds.

**Scale note, because the sentence this paragraph replaced quoted the narrowest of three live figures.** "Roughly six minutes per ten-game run" was record 47's replayed *added* cost (363.524 s ≈ 6.1 min). Beside it stand record 52's intrinsic added cost (292.407 s ≈ 4.9 min) and record 52's **observed** finalise total (1189.729 s ≈ **19.8 min**) — the last being what a real run on a real developer machine actually paid. The decision turns on the largest of the three, not the smallest; a reader taking "six minutes" alone would under-read it by more than 3×.

**The change is output-identical by proof, not by argument — which is the only thing that licenses it, since §3 Impact and Risk Analysis puts "Changing what is measured, or how any score is calculated" out of scope.** `_clusters` is shared: it backs `blunder_eval.score_repetition` (via the `_spec009_clusters` alias) and therefore `metrics.repetition` across all 52 committed records, plus `repetition_experiment`'s own `_near_dup_rate` and `_max_cluster`, i.e. the spec-009 A/B's decision metric. `tests/test_spec043_cluster_skip.py` is the gate, in four parts. **(a) A verbatim copy of the pre-skip implementation as a test oracle**, with the new implementation's whole **partition** asserted equal to it — not merely its count, which two different partitions can share. **(b) Thirty-six generated corpora** (twelve seeded generators × three seeds, at all three reported thresholds) aimed at identical texts, one-character edits, pairs straddling the threshold, empty and mixed-empty pools, texts either side of autojunk's 200-character activation length, heavily repeated characters, lengths spanning 1–500, non-ASCII, and speech-shaped text; plus a pair engineered so that all three `difflib` measures land on **exactly** 0.85, which pins every `<` and the final `>=`. **(c) The ledger as the oracle on real data** — every committed `metrics.repetition.count` was produced by the *pre-skip* code on that run's real pool, so reproducing a committed count from the committed transcripts is an equivalence check against the old implementation without running it. **20 of the 36 ledger-linked transcript dirs** (38 are on disk; two are linked by no record) rebuild to exactly their record's `repetition.denominator`, spanning all three providers and 22 → 902 pooled lines, and **all 20 reproduce both `count` and `rate` exactly — zero differences**; seven run in the default suite, the rest behind `GRAPHIA_SPEC043_FULL_EQUIVALENCE=1` along with a partition-level comparison against the oracle on record 47's full 902-line pool. **(d) Every load-bearing assertion mutation-checked:** the orientation swap is caught by 11 tests including five of the seven real pools; the three boundary comparisons (`<` → `<=` on either bound, `>=` → `>` on the verdict) each by the exactly-on-threshold pair; an over-aggressive bound by 20; an inner-range off-by-one by 56. The control matters as much: *removing* the skip leaves all 86 tests green and only the runtime changes, which is correct — the scores are the contract, the seconds are not.

**One hazard was recorded wrongly in the analysis that proposed this, and the correction makes it worse rather than better.** `SequenceMatcher.ratio()`'s asymmetry was attributed to autojunk keying off the second sequence, which would confine the orientation trap to pooled lines over 200 characters. Measured: `get_matching_blocks`'s recursive longest-match decomposition is not symmetric either, and **82% of random 20-to-90-character pairs measure differently in the two orientations**. So reusing the `b2j` cache with the *smaller* index as `seq2` is a metric change at every pool scale, not only large ones — it moves record 47's count 662 → 664 and record 52's 574 → **572**, in opposite directions. That is why the shipped loop iterates the larger index outer, and why the generated corpus below autojunk's activation length catches the swap too rather than serving as the boring control it was written as.

**Out of scope of this decision, and left alone deliberately.** `eval_dialogue._cluster_near_dups` is a near-verbatim *copy* of the pre-skip function rather than a caller of it, so `make eval-dialogue` still pays the old cost. It is not in the `blunder-eval` path, it scores one run's pooled speeches rather than a growing pool re-scored *N* times, and changing it would need the same equivalence gate for a benefit no measured run is waiting on — a backlog item, not this task's. The persona scorers (`score_persona_near_dup`, `score_persona_sim_sum`) walk their own pairs over a handful of personas, and `score_persona_sim_sum` needs every ratio by definition, so no threshold skip applies there at all.

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
| **A bug in the chunk-splicing damages committed history.** The one risk not recoverable by re-running, and the one atomicity cannot protect against — an atomic write of wrong content is still wrong. | The pre-run backup outside the tracked tree (§2.2b), a hard failure if it cannot be written, and a test asserting every *other* document is byte-identical across a whole run of updates (§4). |
| **A process killed mid-write leaves a truncated document.** This is a real failure of the *current* append path, not of the new one. | Closed by construction: temp file, `fsync`, `os.replace`, `fsync` the directory. A reader or a kill sees the old complete file or the new one. Earlier records are carried across as bytes already read, so no re-render can reformat them. |
| **Partial records get pooled with full baselines by eye.** | The gated marker first in `quality`, the §2.5 invariant, and the `4/10` viewer cell (§2.6). |
| **Partial runs make untracked transcript dirs common.** A partial run leaves an incomplete `evals/transcripts/<run-id>/` linked from `run.transcript_dir`; that directory is what makes the *next* run stamp `dirty: true`. The mechanism does not create the hazard — a full run leaves the same dir — but partial runs will now be frequent where before they left nothing. | Per the author's ruling this is meaningful and stays: the remedy is to commit or remove. Document it in `evals/README.md` beside the existing commit-or-delete norm. |
| **Re-scoring cost, in three classes.** Cheap scalar folds and the rates/Wilson bands re-derived from them; linear passes over the accumulated per-game *lists* (`tally_outcomes(winners, scripted_sides, …)`, third-person self-talk over pooled lines); and **repetition alone superlinear**, being pairwise over accumulated lines. | Measure on a real ten-game run (§2.8) rather than assume; cadence fallback if it bites. |
| **Two concurrent writes race, and the loser's newest update is rolled back one generation.** The residual OCC window (§2.1): both writers pass the hash check, then replace in sequence. | Progressive updates self-heal on the victim's next write. The final write is the exposed one: it retries harder, and on persistent failure writes its document to a named fallback file beside the ledger rather than losing it. `flock` on a gitignored sidecar is the recorded escalation if conflicts are ever observed. |

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
- **Concurrency, tested without concurrency.** The OCC cycle is testable deterministically by injecting a mutation between the read and the replace — a monkeypatched hook that writes a *second* run's document into the file at that instant. Assertions: the write detects the conflict, retries, and the resulting file contains **both** runs' documents, each intact. This is the test that would have caught the truncate-to-offset design the first draft proposed, and it needs no threads, no subprocesses and no timing.
- **Every other document survives a whole run.** With three pre-existing records, a ten-game run's ten updates leave all three byte-identical at every step — asserted on bytes, because a parse-and-re-render round trip is exactly the failure this guards against.
- **The run finds its own document among others.** A ledger already containing a record whose id differs only in the random suffix; the run updates its own and leaves the near-twin untouched. This is the test that earns the collision-proof id (§2.2).
- **Backup preconditions.** The backup is written before the first ledger write; an unwritable backup location fails the run before the ledger is touched.
- **Viewer.** `_resolved_games` / the `Games` cell renders `4/10` for a marked record and `10` otherwise; `_render_quality_section` shows the marker. Both are pure-function tests over hand-built records.

**Not covered by the suite, and stated rather than implied:** that the record survives a real `SIGKILL` or power loss. That is a property of having already written to disk, and the honest verification is one manual kill of a real run — which the mocked suite cannot express and should not pretend to.
