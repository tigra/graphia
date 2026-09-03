# `evals/` — the AI quality ledger

This directory holds **`blunder-ledger.yaml`**, the repo-committed quality
ledger written by `make blunder-eval` (spec 011, _AI Blunder Tracking_) and — on
request, since spec 036 — by `make persona-bench --record`. It turns AI behaviour
from an anecdote into a tracked, comparable, history-backed property of the repo:
each measurement run appends one dated record, and a maintainer answers "Nova vs
Ollama on behaviour X?" or "before vs after prompt change Y?" by reading the
ledger alone. Which **kind** of measurement a record is comes from
[`run.kind`](#runkind), and records compare only **within a kind** as well as
within a provider.

## The ledger contract

- **Append-only.** Each completed run appends **one record** to
  `blunder-ledger.yaml`. A run never overwrites or rewrites an earlier record —
  the file reads chronologically, oldest first.
- **One `---`-separated YAML document per run.** Records are concatenated YAML
  documents, each preceded by a `---` document separator, so the file is a
  valid multi-document YAML stream.
- **Machine-measured fields are never rewritten.** Every field except `notes`
  (`run`, `code`, `provider`, `settings`, `quality`, `metrics`) records what a
  given run actually measured; once written it is immutable. Diff the ledger to
  compare runs — don't hand-edit past records.
- **`notes` is the one human-mutable field.** It alone may be set at run time
  (via `--note`) **and** edited or extended by hand afterwards, including
  multi-line — see [`notes`](#notes--the-one-human-mutable-field) below.
- **Merge conflicts resolve by keeping both documents.** Two runs on diverging
  branches append independently — keep both records on merge.

## Intentionally write-only (for now)

The serializer hand-renders YAML for our one known record shape (see
`src/graphia/tools/blunder_eval.py`, `render_record`) with a **fixed key order**
for clean diffs — deliberately **without** a YAML library, because this format
is one we only ever _write_. There is no reader/parser here on purpose: a
console viewer or before/after comparison tool is a future increment, and _that_
increment is the one that takes on the YAML-parser dependency this one avoids.
For now, read the ledger with a text editor.

## Transcripts

`make blunder-eval` also preserves the **full transcript of every measured
game** (spec 017, _Eval Transcript Preservation_) so a reviewer can read *why* a
number looks the way it does — the public Day discussion and votes **and** the
normally-hidden layers (true roles, the Mafiosos' private Night picks, each
persona including a Mafioso's cover *and* its true self). These are
maintainer-facing eval artifacts, never shown to players in-game.

### Layout

Each run writes one directory under `evals/transcripts/`, named for the run:

```
evals/transcripts/<run-id>/game-NN.txt
```

- **One directory per run.** `<run-id>` is a filesystem-safe, sortable
  timestamp generated once per run (e.g. `2026-06-18T14-32-05`), so runs sort
  chronologically.
- **One `.txt` per game**, with a **zero-padded** game index — `game-01.txt`,
  `game-02.txt`, … (padded to at least two digits, widening for runs of 100+
  games) — so the game ↔ file relationship is obvious and files sort in play
  order.
- Each transcript is a plain, readable file with `<transcript>` / `<setup>` /
  `<night>` / `<day>` / `<round>` structural markers; open it directly, or
  browse it in the viewer (`make view-ledger` → drill into the run).

### Ledger link

The run's record carries **`run.transcript_dir: '<run-id>'`** — the directory
**name** (not an absolute path). The viewer derives the absolute path by joining
the ledger's sibling `transcripts/` directory with that name. The field is
additive: a run that wrote no transcripts, and any record written before spec
017, simply **omits** it.

### Not gitignored — curated commit-or-delete

`evals/transcripts/` is **deliberately not gitignored**. Transcripts are
ordinary untracked files that *hang out* until the developer decides what to do
with them — visibility + curation over a silent ignore. The convention:

- **Commit the full, clean runs** worth keeping (e.g. the n=20 baselines) — a
  deliberate `git add` + commit makes a run part of the shared project record so
  a teammate (or the future LLM-as-Judge) can read the same games.
- **Delete the few-game smoke runs** before committing, once you've confirmed
  they hold no important findings.

There is a **one-command cleanup**:

```
make clean-transcripts
```

It drops every **untracked** run directory under `evals/transcripts/` (the smoke
runs) and **keeps the committed ones** — "untracked vs tracked" is decided by
git, and it only ever operates under `evals/transcripts/`.

**Why clean up before the next measured run.** An uncommitted transcript run
left in the tree makes the **next** eval stamp `code.dirty: true` (uncommitted
changes ⇒ not attributable to a recorded version), so **commit-or-delete before
the next measured run** keeps eval provenance clean. After a smoke run, the
assistant should prompt you to delete that run's transcripts (unless they hold
findings) and commit the full keepers.

## Record shape — field legend

Each record is one YAML document with a **fixed top-level key order** —
`run` → `code` → `provider` → `settings` → `quality` → `outcomes` →
`vote_activity` → `generation` → `metrics` → `notes`. The run-dynamics blocks
all sit in the same band after `quality` and before `metrics`: the two
**game**-dynamics ones (`outcomes`, `vote_activity`) first, then the
**persona-bench**-only `generation` block last in that band, immediately before
the metric family whose denominators it contextualises. All three are
conditional and in practice mutually exclusive — a record carries the game pair
**or** `generation`, never both. `notes` is always last. A full record looks
like:

```yaml
---
run:
  date: '2026-06-13'            # run date — for Bedrock, the only proxy for provider-side model drift
  kind: 'persona-bench'         # spec 036 — WHICH KIND of measurement this is; the key is OMITTED ⇒ a played game
  duration_seconds: 412.3       # wall-clock duration of the whole run (null until finished)
  metrics_version: 1            # rule-set version; bumps when any detection rule or denominator changes
  transcript_dir: '2026-06-13T14-32-05'  # spec 017 — this run's dir under evals/transcripts/ (omitted if no transcripts / older record)
code:
  commit: '<sha>'               # git HEAD at run time — or null if git was unavailable
  branch: 'main'                # git branch — or null if unavailable
  dirty: false                  # true = working copy had uncommitted changes → NOT attributable to a commit
provider:
  name: 'bedrock'               # 'ollama' or 'bedrock'
  large_model: '...'            # resolved gameplay model id (post env-override)
  small_model: '...'            # resolved mechanical model id
  # ── ollama runs only: ──
  models:                       # per-model content fingerprint, so a re-pulled tag with changed weights is distinguishable
    '<name>':
      name: '<name>'
      digest: 'sha256:...'      # content digest, or null if the server didn't report it
  server_version: '0.30.6'      # local Ollama server version, or null if unreachable
  # ── bedrock runs only: ──
  note: 'provider-side model updates are not observable; run date is the only proxy.'
settings:                       # the EFFECTIVE resolved values, so a run can be repeated like-for-like
  large_model: '...'            # resolved gameplay model id actually used
  small_model: '...'            # resolved mechanical model id actually used
  base_url: 'http://...'        # Ollama base URL (null for bedrock)
  games: 5                      # number of games requested
  seed: 20260613                # base structural seed (null = unseeded; game i used seed+i)
  max_days: 12                  # runaway Day cap (spec 023; null = not applicable, e.g. a bench run)
  scripted_player: 'active'     # spec 026 — human-seat stand-in: 'active' or 'passive' (omitted on pre-026 records → read as 'passive')
  private_diaries_enabled: true # spec 039 — which ARM of the private-diaries A/B this run measured; OMITTED (never false) on pre-039 records
  persona:                      # spec 036 — persona-bench runs ONLY: the knobs the generation ran under (whole sub-map omitted for a game run)
    diversity_enabled: true     # the --diversity ARM actually invoked, NOT the ambient config default
    collision_threshold: 0.6    # the similarity bar at which two personas count as too alike
    regen_attempts: 2           # regeneration attempts allowed per collision
    temperature: 1.0            # persona-generation temperature
quality:                        # so a degenerate run cannot masquerade as a clean baseline
  games_attempted: 5            # UNIT FOLLOWS run.kind — games for a game run, ROSTERS for a persona-bench run
  games_completed: 5
  games_failed_early: 0         # games that raised mid-run and were skipped
  # spec 039 — RUN HEALTH, not an AI-quality metric, and no Wilson band (see the `quality` bullet).
  # All three are OMITTED TOGETHER when the run attempted no diary entry — absent, never a 0.0.
  diary_fallback_rate: 0.045    # placeholder entries / entries attempted (derived once, at write time)
  diary_fallback_entries: 2     # diary entries that came out as the node's deterministic placeholder
  diary_entries_attempted: 44   # THE DENOMINATOR — the rate is never written without it
  duration_seconds: 412.3       # same wall-clock duration, mirrored beside the run-quality counts
outcomes:                       # win-rate by side over the COMPLETED games — four buckets that partition the run
  games: 20                     # completed-game denominator (failed-early games excluded)
  law_abiding:                  # a SIDE: carries a win-rate + its Wilson 95% band
    wins: 11
    rate: 0.55                  # wins / games
    ci_low: 0.342
    ci_high: 0.742
  mafia:                        # the other SIDE: same shape
    wins: 6
    rate: 0.3
    ci_low: 0.145
    ci_high: 0.519
  scripted_side:                # spec 027 — the scripted stand-in's-OWN-side win rate (omitted on pre-027 records; equals the matching by-side rate when the seat side is pinned per run)
    side: 'law_abiding'         # which side the scripted stand-in was on
    wins: 11
    rate: 0.55                  # scripted-side wins / all games
    ci_low: 0.342
    ci_high: 0.742
  draw: 2                       # bare count — not a side, no rate
  no_winner: 1                  # winner is null (typically the eval round cap)
  note: 'win-rate is measured against a passive scripted human (always votes No, never initiates) — a consistent comparable measure, not true game balance.'
vote_activity:                  # AI vote-INITIATION counts by side and by game-day — the explicit-zero inverse of `metrics`
  by_side:                      # ALWAYS both side keys with an integer (zero included), never omitted
    law_abiding: 4
    mafia: 0
  by_day:                       # sparse — only days with ≥1 initiation; `by_day: {}` when none
    day_1: 2
    day_2: 1
    day_3: 1
generation:                     # persona-bench runs ONLY — the whole block is omitted for a played game
  collisions: 0                 # casts that ended with an over-similar persona pair (an explicit, measured zero)
  regenerations: 3              # regeneration attempts that fired
metrics:                        # each metric is a rate WITH its denominator visible (never a bare count)
  repetition:
    rate: 0.4                   # count / denominator
    count: 4
    denominator: 10
    ci_low: 0.168               # Wilson 95% lower bound on the true rate (every present metric)
    ci_high: 0.687              # Wilson 95% upper bound — a WIDE band means a small-n, low-trust rate
notes: ''                       # free-text run annotation — the one HUMAN-MUTABLE field (always last)
```

### Field-by-field

- **`run`** — `date` (the run date; for Bedrock it is the *only* proxy for which
  provider-side weights answered), `duration_seconds` (whole-run wall clock,
  `null` until the run finishes), `metrics_version` (the rule-set version — see
  the note on cross-version comparison below), and `transcript_dir` (spec 017 —
  the run's directory **name** under `evals/transcripts/`, e.g.
  `'2026-06-18T14-32-05'`; see [Transcripts](#transcripts) below). **`transcript_dir`
  is a new, additive field:** it is **omitted** on runs that wrote no
  transcripts and on **older records written before spec 017** — read it as
  absent there, exactly like any other pre-feature field. It also carries
  **`kind`** (spec 036 — *which kind of measurement* this record is; **absent ⇒
  a played game**), the field that decides how the rest of the record reads: see
  [`run.kind`](#runkind) below, which is also where the unit of the `quality`
  counts and the compare-only-within-a-kind rule are defined.
- **`code`** — `commit` and `branch` from git at run time (each `null` if git was
  unavailable or the cwd is not a repo), and `dirty`. **`dirty` is the
  load-bearing flag:** `true` means the working copy had uncommitted changes, so
  the run's results are **not attributable to any recorded version** (prompts,
  detection rules, and settings all live in code); a `false`/clean record is
  fully attributable to its `commit`. The harness also prints an up-front stderr
  warning when the tree is dirty.
- **`provider`** — `name` (`ollama` | `bedrock`) plus the resolved `large_model` /
  `small_model` ids. For **ollama** it additionally carries `models` (each model's
  `{name, digest}` content fingerprint — a re-pulled tag with silently changed
  weights is then distinguishable; `digest` is `null` if the server didn't report
  it) and `server_version` (the local server's version, `null` if unreachable).
  For **bedrock** it carries `note` instead — a fixed caveat that provider-side
  model updates leave no client-visible signal, so the run `date` is the only
  proxy for which weights answered.
- **`settings`** — the **effective resolved values actually used** (post
  env-override), so a run can be repeated like-for-like: `large_model`,
  `small_model`, `base_url` (Ollama only; `null` for bedrock), `games`, `seed`
  (base structural seed; `null` when unseeded — game *i* used `seed + i`),
  `max_days` (the spec-023 runaway Day cap; `null` where it does not apply), and **`scripted_player`** (spec 026 — the
  human-seat stand-in used in the run: `'active'` for the deterministic
  rule-based policy or `'passive'` for the prior baseline that never proposes
  and always votes No). **`scripted_player` is a new, additive field:** it is
  **omitted** on records written **before spec 026** — read those as implicitly
  `'passive'`, the only stand-in that existed then — exactly like any other
  pre-feature field. A **persona-bench** record additionally carries the nested
  **`persona`** sub-map (spec 036 — following the `settings.lineup` precedent of
  a one-level nested sub-map): `diversity_enabled`, `collision_threshold`,
  `regen_attempts` and `temperature` — the four knobs the generation actually ran
  under. **`diversity_enabled` is the arm the run was invoked with**
  (`--diversity on|off`), *not* the ambient config default, and that is precisely
  what makes a flag-on / flag-off pair readable **as a pair**: recording the
  default would silently mislabel every flag-off arm. Conditional and additive
  like every other new field — a game run omits the whole sub-map, so existing
  records are untouched. A record also carries
  **`private_diaries_enabled`** (spec 039) when the run could state which arm of
  the private-diaries A/B it measured — see
  [`settings.private_diaries_enabled`](#settingsprivate_diaries_enabled) below,
  which is also where the absence rule and the definition of a valid pair live.
- **`quality`** — run-quality counts so a degenerate run can't pass as a clean
  baseline: `games_attempted`, `games_completed`, `games_failed_early` (games
  that raised mid-run and were skipped), and `duration_seconds` (mirrored from
  `run`). **The unit these count is defined by `run.kind`** — games for a game
  run, **rosters** for a `persona-bench` one. The key *names* are deliberately
  reused rather than forked into a parallel set, so one renderer and one viewer
  keep serving both kinds; `run.kind` is the single field that says what is being
  counted. See [`run.kind`](#runkind).

  A run that **attempted at least one diary entry** additionally carries three
  flat spec-039 keys, between `games_failed_early` and `duration_seconds`:
  **`diary_fallback_rate`**, **`diary_fallback_entries`** and
  **`diary_entries_attempted`**.

  - **What they are for.** The diary node writes a fixed, deterministic
    placeholder whenever its structured-output call fails, and a run that
    measured mostly placeholder text is **not a measurement of diary content at
    all** — it just looks like one. A 1-game smoke produced **9 of 11**
    byte-identical placeholder entries and said so nowhere: not in the ledger,
    not in the transcript, and not in the log (`blunder_eval` installs no logging
    handler, so the node's `logger.exception` is *not* an observability channel
    for a measured run). These keys make a diaries-on arm **self-validating** —
    a run reading `diary_fallback_rate: 0.82` is visibly not a measurement of
    diaries, where before it was indistinguishable from a clean one. Read the
    rate **beside its `diary_entries_attempted` denominator**, always; the
    denominator is small (a handful of entries per game) and the rate alone
    reads as certainty it has not earned.
  - **Run health, not a metric — so no Wilson band.** They sit in `quality`
    beside `games_failed_early`, the other "did this run measure anything?"
    count, and deliberately **not** in `metrics`: `METRIC_ORDER` and the metric
    tail are untouched. `quality` is a **census of one run** rather than a sample
    of a population — `games_failed_early: 3` is an exact count, which is why
    nothing in this block carries an interval — and the observed fallbacks
    **cluster by fan-out** (a whole Day's entries fail together), violating the
    independent-Bernoulli assumption a Wilson band rests on, in the direction
    that would make the band a lie.
  - **Flat, and the rate is derived once.** Three flat scalars matching
    `quality`'s existing shape, not `metrics`' nested `{rate, count,
    denominator}` facet — this must not *read* as a scored metric. The rate is
    computed at write time from the count and the denominator, so there is exactly
    one definition of it; the viewer displays the recorded value rather than
    recomputing it.
  - **Absent, never a misleading zero.** All three are **omitted together** when
    the run attempted no diary entry — a diaries-off arm, a `persona-bench`
    record, or any record written before spec 039 — exactly like a metric whose
    denominator was 0. Absence means *no opportunity*, and `0.0` is the distinct,
    genuinely *clean* reading. The gate is the **denominator**, not the arm
    label: an off-arm run that somehow *did* write entries (an ADR-011 parity
    break) is counted rather than hidden, and an on-arm run that had no
    opportunity records nothing rather than a flattering `0.0`.
- **`outcomes`** — win-rate by side over the run's **completed** games (so a
  reader can ask "did this fix help one side win more?"); see
  [`outcomes`](#outcomes) below.
- **`vote_activity`** — AI vote-**initiation** counts by side and by game-day
  (so a silent-Day provider reads as a visible `0`, not an absence); see
  [`vote_activity`](#vote_activity) below.
- **`generation`** — the persona-**generation process** counts of a
  persona-bench run: `collisions` (how many casts ended up containing a pair of
  personas judged too alike) and `regenerations` (how many regeneration attempts
  fired). It is deliberately **its own block** rather than part of `quality`
  (which is run *health* — how many units were attempted and completed) or of
  `metrics` (the versioned scored family): a collision count is neither. Read it
  **beside** the persona similarity facets in `metrics`, never instead of them —
  the collision *count* is what carried the spec-034 comparison (two casts in
  ten shipping a near-duplicate versus none in ten), a result a similarity
  *mean* alone would have lost. Like `vote_activity`, a present block emits its
  **explicit zeroes**: `collisions: 0` is a measured finding, not a
  no-opportunity absence. **Persona-bench runs only** — a played game populates
  nothing here, so the **whole block is omitted**, exactly as `outcomes` /
  `vote_activity` are omitted from a bench record; records written before this
  block landed simply lack it, like any other pre-feature field.
- **`metrics`** — a map of metric-name → `{rate, count, denominator, ci_low,
  ci_high}` (`rate` = `count / denominator`). The six watched behaviours, each
  AI-only (the human player is never counted):
  - **`repetition`** — AI Day lines that are name-masked near-duplicates of
    another AI line (the spec-009 measure, difflib ratio ≥ 0.85). *Denominator:
    AI spoken lines.*
  - **`third_person_self_talk`** — AI Day lines in which the speaker names
    *itself* (its own name, word-boundary, case-insensitive) as if it were
    another player. *Denominator: AI spoken lines.*
  - **`self_vote.initiation`** — an AI starting a vote against **itself** (counted
    from the raw structured-output payload — the game's turn-handler rejects it
    before it reaches state, so this is the one blunder no post-game record can
    see). *Denominator: all AI vote-initiation attempts.*
  - **`self_vote.yes`** — an AI casting a **Yes** ballot on its **own**
    execution. *Denominator: AI ballots where the voter is the vote's target
    (self-execution opportunities).*
  - **`peer_vote.initiation`** — a Mafioso starting a vote against a **fellow
    Mafioso**. *Denominator: all Mafioso-AI vote initiations.*
  - **`peer_vote.yes`** — a Mafioso casting a **Yes** ballot on a fellow
    Mafioso's execution (bussing). *Denominator: Mafioso-AI ballots cast on a
    Mafia target.*

  **Absent ≠ 0.** A metric whose denominator was 0 — the game offered **no
  opportunity** for that blunder (e.g. no ballot was ever cast on a Mafia target,
  so `peer_vote.yes` was never tested) — is **omitted from the record entirely**,
  not reported as `rate: 0.0`. A `0.0` would read as "the AI never did it" when in
  truth it was never tested. So a metric simply not appearing in a run's record
  means *no opportunity arose*, not *measured zero*. (The two speech metrics —
  `repetition`, `third_person_self_talk` — share the "AI spoken lines"
  denominator, which is always > 0 in a real game, so they stay present with a
  genuine `0.0` when clean.)

  **`ci_low` / `ci_high` — the Wilson 95% confidence interval.** Every *present*
  metric carries a closed-form [Wilson score interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval)
  for the true rate at a 95% confidence level: `ci_low` is the lower bound,
  `ci_high` the upper, each clamped to `[0, 1]`. It exists so a reader can judge
  **per-metric reliability from the band's width** — `repetition 0.45` over
  `denominator: 108` is a tight, trustworthy band, whereas `self_vote.yes 0.50`
  over `denominator: 2` is a very wide one (≈ `0.09 … 0.91`): the same rate, but
  noise, not signal. The interval is **derived/supplementary** — computed from
  `count` and `denominator` alone, it changes no detection rule, so adding it did
  **not** bump `metrics_version` and rates stay cross-comparable. **Caveat for
  `repetition`:** the interval treats each line as an independent Bernoulli
  trial, but near-duplication is *correlated within a game* (an AI that loops
  tends to loop repeatedly in the same game), so for `repetition` the band
  **understates** the true uncertainty — an accepted tradeoff for a closed-form
  interval that works at any `n`. **Records written before this field landed do
  not carry `ci_low` / `ci_high`** (read those rates without a band, as with any
  pre-provenance field below).

  **Value-type facets carry no `rate` and no CI.** Beside the six rate metrics
  above, the persona-similarity facets (specs 031–033) are not binomial
  proportions but *similarities*, so they render as `{mean | peak, denominator}`
  — a `mean` or a `peak` plus the number of persona pairs it was taken over — and
  deliberately carry **no** `rate`, `count`, `ci_low` or `ci_high`: a Wilson
  interval on a similarity would be meaningless. There are four:
  **`persona_lex_mean` / `persona_lex_peak`** (the free, fully local `difflib`
  word-level measure) and **`persona_sem_mean` / `persona_sem_peak`** (the
  meaning-based measure, from Bedrock Titan Text Embeddings v2 — the paid
  instrument, and deliberately **always Bedrock** regardless of which provider is
  under test, so the measuring stick does not change with the model being
  measured). The semantic pair is **omitted entirely** when it was not measured —
  `--semantic` not passed, or embeddings unavailable — never reported as `0.0`:
  the same **Absent ≠ 0** rule as above. A **`persona-bench`** record's `metrics`
  block is these facets and nothing else, because it plays no game and so offers
  no opportunity for any of the six rate metrics.
- **`notes`** — the one human-mutable field; always last. See below.

### run.kind

`run.kind` (spec 036, _Persona-Generation Measurements Join the Tracked Quality
History_) names **which kind of measurement** a record describes. Read it first:
it decides what everything below it means.

- **Absent ⇒ a played game.** A game run omits the key entirely, so every record
  written before spec 036 keeps its exact original meaning and **nothing was
  backfilled**. Absence here is *meaningful*, not unknown — which is why the
  viewer's `Kind` column shows `game` for such a record rather than a blank.
- **`'persona-bench'`** — an isolated persona-generation measurement written by
  `make persona-bench` with `--record`: characters generated and scored, **no
  game played**. Recording is **opt-in**; without `--record` the bench prints its
  summary and leaves the ledger completely untouched (the bench's value is
  dev-loop speed, so auto-recording would bury the rare real measurement under
  throwaway runs).
- **It defines the unit of the `quality` counts** — games for a game run,
  **rosters** for a persona-bench run — and `settings.games` likewise carries the
  **roster** count on a bench record.
- **The game-only parts are omitted, not zeroed.** A bench record carries no
  `outcomes`, no `vote_activity` and no `run.transcript_dir`: no game was played,
  so there is no winner, no ballot and no transcript to link. Blank game figures
  on a bench record therefore mean *no game was played* — **not** that a game
  failed part-way (that case is `quality.games_failed_early` on a record whose
  kind *is* a game). For the same reason a bench run's `seed` and Day cap render
  as `null` — "did not apply" — rather than borrowing an ambient config value
  that never ran. Conversely `settings.persona` and the `generation` block appear
  on a bench record only.

**Records compare only within a kind — as well as within a provider.** The
ledger already carries a within-provider rule (an Ollama record and a Bedrock
record are different measurements, which is why runs are made **per provider**)
and a within-`metrics_version` rule (see [Versioning and older
records](#versioning-and-older-records)). **Kind is a third axis of the same
discipline, and the strictest of the three**, because it is the one that can look
comparable when it is not: a persona-bench record and a game record can share
metric *keys* — the persona facets are written by both paths — yet they are not
the same measurement. The bench pools its pairs across N generated casts with no
game ever played, while a game record's facets come out of full games that also
drive Days, votes and deaths; and the two records' `quality` counts are not even
in the same unit. Comparing across kinds reads a difference in *what was
measured* as a difference in *quality*. So a comparison means something only when
**kind, provider and `metrics_version` all match**: diff two bench records
against each other, or two game records — never one of each. Filtering the viewer
by `Kind` before reading down a column is the practical form of the rule.

### settings.private_diaries_enabled

`settings.private_diaries_enabled` (spec 039, _Per-AI Private Diaries_) is the
**arm label**: which side of the diaries-on / diaries-off comparison a run
measured. A boolean, recorded from the arm the run was actually **invoked** with
(`make blunder-eval ARGS="--diaries on|off"`), *not* the ambient config default —
recording the default would silently mislabel every off arm, the same trap
`settings.persona.diversity_enabled` documents. The harness **refuses to start**
rather than guess: a config that cannot answer fails before game 1, because a
record without a truthful arm label is worthless and 30 minutes of tokens spent
on an unlabelled record cannot be recovered (the ledger is append-only).

**Absence is a third case, and it does not mean `false`.** The ledger already has
two absent-means-something fields — `settings.scripted_player` (absent ⇒ read as
the prior default `'passive'`) and `run.kind` (absent ⇒ "a played game"). This is
neither. A record without this key was played by a build in which **the diary
feature did not exist at all**; it is not the claim "this run measured the off
arm". So it renders **blank** in the viewer — both in the `Diaries` column and in
the drill-down, which gains no line at all — and never `false`, which would
assert a measurement nobody made. The practical consequence: **a pre-039 record
is not the other half of an A/B pair.** All 30 records committed before spec 039
omit the key, and none of them is a control arm for a diaries-on run.

**What a valid pair is.** Two **spec-039-era** records — both carrying the key —
that agree on everything except the arm:

- the same **`code.commit`** (and both `code.dirty: false`, or the numbers are
  not attributable to any recorded version at all);
- the same **`provider.name`** (an Ollama record and a Bedrock record are
  different measurements — runs are made per provider);
- the same **`run.kind`** (see [`run.kind`](#runkind));
- the same **`run.metrics_version`** (see [Versioning and older
  records](#versioning-and-older-records));

…and differing **only** in `private_diaries_enabled`. Anything else that differs
between the two records is a second variable, and a difference in the numbers can
then no longer be attributed to the diaries. On a diaries-**on** arm, read
[`quality.diary_fallback_rate`](#record-shape--field-legend) before reading the
arm's results: a high fallback share means the on arm measured the deterministic
placeholder rather than diary content, so the pair is not a comparison of what it
claims to be.

### outcomes

The `outcomes` block is a win-rate snapshot over the run's **completed** games
(spec 013, _AI Behavioral Integrity & Outcome Tracking_):

- **`games`** — the completed-game count, and the **single denominator** for the
  whole block. Games that raised mid-run never produce a winner, so they are
  excluded here (they are already counted in `quality.games_failed_early`).
- **`law_abiding` / `mafia`** — the two **sides**, each a `{wins, rate, ci_low,
  ci_high}` map: `wins` is that side's win count, `rate` = `wins / games`, and
  `ci_low` / `ci_high` are the **Wilson 95%** band on that win-rate (the same
  interval `metrics` uses — judge reliability by its width). When `games == 0`
  the side renders as a bare `{wins: 0}` with **no** `rate` / `ci_low` /
  `ci_high` (a 0/0 win-rate would be meaningless).
- **`scripted_side`** (spec 027) — the win rate of *the side the scripted
  stand-in was on*: a `{side, wins, rate, ci_low, ci_high}` map where `side`
  names that side (`law_abiding` or `mafia`), `wins` is the count of games the
  scripted side won, `rate` = scripted-side wins **÷ all games**, and `ci_low` /
  `ci_high` are the same **Wilson 95%** band. It is counted **per game** (a game
  is a scripted-side win iff `winner` equals *that game's* seat side), so a
  `no_winner` / `runaway` game counts toward the denominator but **never as a
  win** — exactly like the by-side rates. **It equals the matching by-side
  rate** (`law_abiding.rate` or `mafia.rate`) **when the seat side is pinned per
  run** — the spec-026 default — so it is the *one comparable number* across a
  Law-abiding batch and a Mafia batch. When `games == 0` it renders as a bare
  `{side, wins: 0}` (rate/CI omitted, like the sides). **It is a derived view,
  not a partition bucket**, so it is excluded from the partition invariant below.
  **Additive — not retro-filled:** records written **before spec 027** (and any
  run that resolved no seat side) simply **omit** it — read it as absent, exactly
  like any other pre-feature field.
- **`draw` / `no_winner`** — **bare integer counts**, not sides, so neither
  carries a rate or a CI. `draw` is a finished game with no winning side;
  `no_winner` is a game whose `winner` was `null` — **dominated by the eval
  round cap**, since the scripted human always votes No, so a game that can't
  reach a decisive execution simply runs out of rounds unresolved.
- **`note`** — a **fixed, machine-emitted** caveat string (immutable, like
  `provider.note` for bedrock — *not* the human-mutable top-level `notes`).

**Partition invariant (a reader can sanity-check it):** the four buckets are
mutually exclusive and exhaustive over the completed games, so

```
law_abiding.wins + mafia.wins + draw + no_winner == games
```

always holds in a well-formed record — if it doesn't, the record is suspect.

**Passive-scripted-human caveat (the load-bearing one).** Every eval game is
played against the **scripted law-abiding human** who *always votes No and never
initiates a vote*. That makes the win-rate a **consistent, comparable measure
across runs** (Nova vs Ollama, before vs after a prompt change) — but it is
**NOT a true game-balance figure**, because a real human plays nothing like that
passive script. Read `law_abiding` vs `mafia` as "did this change shift the
balance *under the fixed eval opponent*", never as "is the game balanced". This
same caveat rides in the machine-emitted `outcomes.note` so it travels with
every record, not just this README.

### vote_activity

The `vote_activity` block counts **AI vote-initiation attempts** (an AI calling
a vote, by the public announce line), summed across the run's completed games
and bucketed two independent ways (spec 013):

- **`by_side`** — a map with **always both keys**, `law_abiding` and `mafia`,
  each a plain **integer count** (zero included) of vote initiations made by AI
  players on that side.
- **`by_day`** — a **sparse** map, `day_1`, `day_2`, … keyed by game-day, only
  for days that saw **at least one** initiation; days sorted by their integer
  suffix (`day_2` before `day_10`). When no day saw an initiation it renders as
  the literal **`by_day: {}`** (present-but-empty), never an omitted key.

**Explicit-zero — the deliberate divergence from `metrics`.** This is the whole
point of the block, so it is stated plainly: unlike `metrics`, which **omits** a
no-opportunity rate entirely (a `0.0` there would misread as "the AI never did
it" when in truth it was never tested — see **Absent ≠ 0** under `metrics`),
`vote_activity` **always emits its zero**. A run where the AI never initiates a
vote renders `by_side: {law_abiding: 0, mafia: 0}` / `by_day: {}` — a
**committed, visible zero**, never an absent block. The reason for the opposite
treatment: here the **absence of Day activity is itself the signal** (e.g. a
provider whose Day phase is silent — the AI never speaks up to call a vote), so
the zero must survive into the record and the viewer rather than vanishing.

`by_side` and `by_day` are **independent marginals of the same grand total** —
both partition the identical set of counted initiations, just along different
axes, so `sum(by_side.values()) == sum(by_day.values())` in any record.

## `notes` — the one human-mutable field

Every record ends with a top-level **`notes`** key: a free-text annotation of
*why* the run was made or *what* was observed. It is the **single exception** to
"never rewrite history":

- **Set it at run time** with `--note "<free text>"` (e.g.
  `make blunder-eval ARGS="--provider bedrock --games 5 --note 'baseline before prompt change Y'"`).
- **Or leave it off** — the record then renders as `notes: ''` (present but
  empty), visibly inviting you to **edit or extend it by hand** afterwards.
- **Multi-line is allowed.** Hand-write it as a YAML literal block scalar so it
  stays valid YAML:

  ```yaml
  notes: |
    first observation
    second observation
  ```

  (The harness emits this same block-scalar form automatically when a `--note`
  contains newlines.)

Only `notes` is hand-editable; every **machine-measured** field (`run`, `code`,
`provider`, `settings`, `quality`, `metrics`) stays **append-only and is never
rewritten**.

## Versioning and older records

- **`outcomes.note` misdescribes the scripted human on records dated 2026-06-21
  onward. Trust `settings.scripted_player`, not the note.** Seventeen records
  carry a note asserting *"a passive scripted human (always votes No, never
  initiates)"* while `settings.scripted_player` on the **same record** reads
  `active`. The setting is right and the note is wrong. Cause: `outcomes.note`
  was a single hard-coded string, written for spec 013 when the passive
  stand-in was the only one there was; spec 026 introduced the active stand-in
  and **made it the default** without revisiting the note, so it went stale
  silently and stayed that way for ten weeks. Affected runs: `2026-06-21`
  (bedrock, ollama), `2026-06-22` (bedrock, ollama), `2026-08-30` (bedrock ×2,
  bedrock-claude, ollama), `2026-08-31` (bedrock-claude, ollama), `2026-09-02`
  (bedrock ×2, bedrock-claude ×2, ollama ×3).

  Records **older** than that which carry the same passive note and **no**
  `settings.scripted_player` field are **correct** — passive was the only
  behaviour when they were written. The absence of the field means passive, as
  the field legend already states.

  The seventeen notes are **left as written**, because this ledger is
  append-only and a committed record is not edited after the fact; the
  correction lives here instead, and `git log` holds the original text either
  way. Fixed at the source on 2026-09-03: `outcomes.note` is now **derived**
  from the run's actual stand-in rather than restated beside it, and both
  branches are pinned by test. The general lesson, worth applying to any future
  machine-emitted note: a note describing a **configurable** condition has to
  be computed from that condition, or it becomes a second source of truth that
  drifts from the first without anything failing.

- **Persona facets written before spec 036 carry no value.** `render_record`
  filtered metric sub-keys through a tuple containing neither `mean` nor `peak`,
  so every value-type facet from specs 031/032/033 reached the ledger as a bare
  `denominator:` with the measured figure **silently dropped**. Four records are
  affected (the three 2026-08-30 n=10 arms and the Nova n=50). Those numbers were
  computed and then discarded at write time — they exist nowhere and **cannot be
  backfilled**. Read a bare `denominator:` on a persona facet as *this writer
  defect*, not as an unmeasured run. Fixed in spec 036; records written from
  2026-08-31 onward carry the value. (The records near the top of the file that
  *do* have `mean:` were hand-written by the specs-032/033 backfill script, which
  is why they escaped.)

- **`metrics_version` bumps invalidate cross-version comparison.** It is the
  single source of truth for the rule set behind every metric; any change to a
  detection rule or a denominator definition bumps it. Rates measured under
  **different** `metrics_version` values are **not directly comparable** — the
  bump is the in-ledger signal that the numbers were produced under different
  rules.
- **Early records may lack some keys.** Records written before the full
  provenance block landed may be missing some `code` / `provider` / `settings`
  fields (or even the whole block). That is expected for pre-provenance runs and
  is not a corruption — read those records for what they carry, and prefer the
  newer, fully-attributable ones for any version-to-version comparison.
- **Records written before spec 013 lack `outcomes` / `vote_activity`.** These
  two game-dynamics blocks landed after the original record shape, so older
  records simply don't carry them — read them as **absent**, exactly as any
  other pre-provenance field. Their arrival **did not bump `metrics_version`**:
  they are orthogonal new *measurements* (win-rate, vote-initiation activity),
  not a change to the blunder-family detection rules, so bumping would falsely
  flag every prior blunder rate as incomparable. This is the same precedent as
  the `ci_low` / `ci_high` reliability band — a derived/supplementary
  measurement is not a rule change. So a blunder `rate` stays cross-comparable
  across the spec-013 boundary; only the new blocks are missing from earlier
  records.
- **Records written before spec 036 lack `run.kind`, `settings.persona` and
  `generation`.** The missing `run.kind` is **not** a gap to be filled: per the
  contract above, its absence *means* "a played game", which is what every
  pre-036 record is. The other two belong to a kind of run that did not exist
  yet. Their arrival **did not bump `metrics_version`** either — a record kind
  plus two new provenance/process blocks change no detection rule and no
  denominator definition, so every blunder `rate` stays cross-comparable across
  the spec-036 boundary, on the same precedent as `ci_low` / `ci_high` and the
  spec-013 blocks above. Comparability *across kinds* is a separate and stricter
  matter — see [`run.kind`](#runkind).
- **Records written before spec 039 lack `settings.private_diaries_enabled` and
  the three `quality.diary_*` keys.** All 30 records committed before spec 039
  omit them. Neither absence is a gap to be backfilled, and the two mean
  different things: the missing **arm label** means *the diary feature did not
  exist in that build*, so such a record is **not** the off arm of an A/B pair
  (see
  [`settings.private_diaries_enabled`](#settingsprivate_diaries_enabled)), while
  the missing **`diary_*`** keys mean *no diary entry was attempted*, the same
  **Absent ≠ 0** rule the metric family follows. Their arrival **did not bump
  `metrics_version`**: one settings field plus one run-health census change no
  detection rule and no denominator definition, no scorer reads either of them,
  and `METRIC_ORDER` and the metric tail are untouched — so every blunder `rate`
  stays cross-comparable across the spec-039 boundary, on exactly the precedent
  of `ci_low` / `ci_high`, the spec-013 `outcomes` / `vote_activity` blocks and
  the spec-036 fields above. A bump would have falsely flagged all 30 committed
  rates as incomparable. What the arm label does instead is **narrow** the
  comparability contract, in the one place it can be read — the record itself.
