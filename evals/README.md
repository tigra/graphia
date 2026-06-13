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
- **Records accumulate; history is never rewritten.** The ledger is committed
  alongside the code. Diff it to compare runs; don't hand-edit past records.
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

## Record shape (Slice 1)

Each record currently carries:

```yaml
---
run:
  date: '2026-06-13'        # run date (the only proxy for provider-side model drift)
  games: 5                  # games attempted this run
  metrics_version: 1        # bumps when a detection rule or denominator changes
provider:
  name: 'bedrock'           # 'ollama' or 'bedrock'
  large_model: '...'        # resolved gameplay model (post env-override)
  small_model: '...'        # resolved mechanical model
quality:
  games_attempted: 5
  games_completed: 5
  games_failed_early: 0
metrics:
  repetition:               # each metric is a rate with its denominator visible
    rate: 0.4
    count: 4
    denominator: 10
notes: ''                   # free-text run annotation — the one HUMAN-MUTABLE field
```

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

Only `notes` is hand-editable; every **machine-measured** field (`run`,
`provider`, `quality`, `metrics`, and the later provenance blocks) stays
**append-only and is never rewritten**. Records produced before this field was
introduced may simply lack a `notes` key — that is fine.

Later increments grow the record (code commit/branch/dirty provenance, the full
blunder-metric family, Ollama model digests / Bedrock model note, effective
settings, run duration) — always **added** keys, never a rewrite of the contract
above. `notes` always stays last.
