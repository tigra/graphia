---
spec: 011-ai-blunder-tracking
spec_title: AI Blunder Tracking (Repo-Persisted Quality Ledger)
introduced_on: 2026-06-13
---

# Concepts introduced in this increment

## Measurement design

- **Repo-persisted metric ledger (baby MLOps)** (`repo-persisted-metric-ledger`) — Each eval run appends one record to an append-only, write-only YAML file committed *inside the repo*, so AI quality becomes a diffable, history-backed property instead of a terminal scroll-back: measure → commit the record → change something → measure → compare by reading two records.
- **Run provenance for attributability** (`run-provenance-for-attributability`) — A record carries the exact code state (git commit + a clean/dirty flag, with an up-front warning when dirty), model fingerprints (Ollama digests + server version; the full cloud model id + an "updates are invisible" caveat), the effective resolved settings, and a metrics-definitions version — so any number in the ledger is traceable to the precise code and model that produced it.
- **Absent ≠ zero** (`absent-not-zero`) — A metric whose denominator is 0 (the game never created the opportunity) is *omitted* from the record rather than rendered as a misleading `0.0`, because "never tested" and "tested, never happened" are different facts.
- **The one human-mutable field in an append-only log** (`human-mutable-notes-field`) — A free-text `notes` field is the single field a maintainer may edit/extend after a run (the *why* beside the machine's *what*), carved out as the explicit exception to the ledger's otherwise-immutable, never-rewritten records.

## Statistics

- **Wilson confidence interval per metric** (`wilson-ci-per-metric`) — Each rate ships with a closed-form Wilson 95% score interval so reliability is readable at a glance (tight band at large n, wide band at n=2), with an honest documented caveat that treating correlated observations as independent understates the band for the clustered repetition measure.

## Capture & attribution

- **Measuring the attempt the safety net rejects** (`capture-absorbed-attempt-as-metric`) — Some blunders never reach game state because a validator rejects them first; intercepting the raw structured-output payload at the LLM seam turns an *invisible rejected attempt* into a counted behavioral metric (here, an AI's self-targeted vote that the turn-handler discards).
- **Prompt-derived actor attribution** (`prompt-derived-attribution`) — To attribute a captured action to the AI that produced it, read the actor's identity from the invoke *prompt the call was handed* (which names the speaker) rather than a re-entrant `graph.get_state()` — sidestepping the stale mid-stream-snapshot trap.

## Testing

- **Template-derived parsing as a reword tripwire** (`template-derived-parsing`) — Build the extraction regexes by importing the game's own message-format strings and escaping their literal spans, so a reword of a template breaks the parse loudly in tests instead of silently mis-counting in production.
