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
```

Later increments grow the record (code commit/branch/dirty provenance, the full
blunder-metric family, Ollama model digests / Bedrock model note, effective
settings, run duration) — always **added** keys, never a rewrite of the contract
above.
