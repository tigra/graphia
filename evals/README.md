# `evals/` — the AI quality ledger

This directory holds **`blunder-ledger.yaml`**, the repo-committed quality
ledger written by `make blunder-eval` (spec 011, _AI Blunder Tracking_). It
turns AI behaviour from an anecdote into a tracked, comparable, history-backed
property of the repo: each measurement run appends one dated record, and a
maintainer answers "Nova vs Ollama on behaviour X?" or "before vs after prompt
change Y?" by reading the ledger alone.

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

## Record shape — field legend

Each record is one YAML document with a **fixed top-level key order** —
`run` → `code` → `provider` → `settings` → `quality` → `metrics` → `notes`
(`notes` always last). A full record looks like:

```yaml
---
run:
  date: '2026-06-13'            # run date — for Bedrock, the only proxy for provider-side model drift
  duration_seconds: 412.3       # wall-clock duration of the whole run (null until finished)
  metrics_version: 1            # rule-set version; bumps when any detection rule or denominator changes
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
  max_rounds: 10                # per-game Day-round cap (null = uncapped)
quality:                        # so a degenerate run cannot masquerade as a clean baseline
  games_attempted: 5
  games_completed: 5
  games_failed_early: 0         # games that raised mid-run and were skipped
  duration_seconds: 412.3       # same wall-clock duration, mirrored beside the run-quality counts
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
  the note on cross-version comparison below).
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
  (base structural seed; `null` when unseeded — game *i* used `seed + i`), and
  `max_rounds` (`null` when uncapped).
- **`quality`** — run-quality counts so a degenerate run can't pass as a clean
  baseline: `games_attempted`, `games_completed`, `games_failed_early` (games
  that raised mid-run and were skipped), and `duration_seconds` (mirrored from
  `run`).
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
- **`notes`** — the one human-mutable field; always last. See below.

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
