# Technical Specification: AI Blunder Tracking (Repo-Persisted Quality Ledger)

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

One new make-gated harness — `src/graphia/tools/blunder_eval.py`, `make blunder-eval` — that composes four already-proven pieces:

1. **The scripted-game driver** (from `eval_dialogue.py` / `ollama_smoke.py`): play N unattended games against the **real** selected provider, scripted human, isolated checkpoints.
2. **The capture proxy** (generalizing `ollama_smoke.py`'s `_CountingModel`): wrap the tier clients via the established `llm._active_provider`/`_large`/`_small` seams to record **raw structured-output payloads** — this is what makes *absorbed attempts* visible (an AI's self-vote initiation that `_accept` rejects never reaches game state; the proxy sees the raw `DayAction` before the safety net does).
3. **The game's own record** (post-game state + message history): roles from `players`, vote initiations/ballots from our own message templates (`VOTE_INITIATE_ANNOUNCE_TEMPLATE`, `VOTE_PER_BALLOT_TEMPLATE`) — exact data for the action-based metrics; named `AIMessage`s for the speech-based ones; `_mask_names`/near-dup imported from `repetition_experiment.py` for the repetition metric.
4. **A provenance block + YAML ledger**: git identity (commit + dirty flag, with an up-front warning), Ollama model **digests** and server version (both verified live against this host: `/api/tags` carries `digest`, `/api/version` → `0.30.6`), resolved settings, seeds, metric-definitions version, run-quality counts — appended as one YAML document to **`evals/blunder-ledger.yaml`** (repo root; YAML chosen over wide JSONL for readability; a console table viewer is explicitly out of scope).

No production code changes — the game is measured, not modified (functional-spec §2.4).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Metric definitions and their data sources — **[Agent: langgraph-agentic]**

Action-based (**exact**), from raw proxy capture + game record, with roles from final `players`:

| Metric | Numerator events | Denominator (visible in record) |
| --- | --- | --- |
| `self_vote.initiation_attempts` | raw `DayAction(kind="vote", target_id == speaker.id)` from the proxy (speaker resolved at invoke time via a live-state callback, the `dynamic_night_pointing` pattern) — counted even though `_accept` rejects them | all raw AI vote-initiation attempts |
| `self_vote.own_execution_yes` | AI Yes ballot where voter == vote target (from per-ballot messages + the preceding announce) | all AI ballots where voter == target (i.e. opportunities) |
| `mafioso_peer_vote` (combined per functional-spec §2.1) | mafia-AI initiations targeting a fellow mafioso **+** mafia-AI Yes ballots on a fellow mafioso | mafia-AI initiations **+** mafia-AI ballots cast on mafia targets |

Speech-based (**approximate by design**, functional-spec §2.1 last criterion), over named `AIMessage`s:

| Metric | Rule (documented as code constants) | Denominator |
| --- | --- | --- |
| `third_person_self_talk` | the speaker's own name appears (word-boundary, case-insensitive) in their own spoken line | AI spoken lines |
| `self_accusation` | own name within a small token window of a documented **suspicion lexicon** (`suspicious`, `suspect`, `accuse`, `guilty`, `mafia`, …) in their own line — a subset-ish of the above, counted independently | AI spoken lines |
| `repetition` | the spec-009 name-masked near-dup rate at 0.85 (imported, not reimplemented) | AI spoken lines |

A module-level **`METRICS_VERSION = 1`** stamps every record; any change to a rule, lexicon, or denominator bumps it (functional-spec §2.3).

### 2.2 Capture instrumentation — shared, provider-agnostic — **[Agent: langgraph-agentic]**

- Extract `ollama_smoke.py`'s counting proxy into a small shared helper (e.g. `src/graphia/tools/instrument.py`) extended to record `(schema, raw result, speaker-id-at-invoke)` — `ollama_smoke` keeps working against the same helper (no behavior change there).
- The proxy installs via the documented in-process seams (`llm._active_provider`, `llm._large`, `llm._small`) — identical for both providers, since the seam sits *above* the provider branch (ADR 009's dividend).
- Game-record extraction parses **our own** message templates from `graphia.prompts` (parse anchors imported, not duplicated) plus `players` roles — exact, no LLM-output parsing.

### 2.3 Provider selection and isolation — **[Agent: python-backend]**

- CLI: `--provider {ollama,bedrock}` (plus `--games`, `--seed`, `--max-rounds`, optional model overrides). Forces `GRAPHIA_LLM_PROVIDER` in-process; `ollama` runs the existing preflight first.
- **Both** providers get the smoke's env isolation (pop `GRAPHIA_*MEMORY_ID`/`GATEWAY*`/`STATS_STRATEGY_ID`): eval games must never pollute the career-stats stores — for Bedrock runs this matters because the offline config gate only covers `ollama`.
- Bedrock runs need live AWS credentials and cost real tokens; stated in `make help` text and the README evals table.

### 2.4 Provenance block — **[Agent: python-backend]**

Collected once per run, before games start:

- **Code:** `git rev-parse HEAD` + `git rev-parse --abbrev-ref HEAD`; dirty = `git status --porcelain` non-empty → **warn to stderr up front** ("results will not be attributable to a recorded version") and set `code.dirty: true` in the record. Run proceeds (functional-spec §2.3).
- **Models:** `ollama` → per-model `digest` + server `version` from `/api/tags` / `/api/version` (verified present, this host, Ollama 0.30.6); `bedrock` → the full model ids from `llm._LARGE_MODEL_ID`/`_SMALL_MODEL_ID` + a fixed `note: provider-side model updates are not observable; run date is the only proxy`.
- **Effective settings:** resolved model names/base URL (post-env-override, from `load_config()`), games, base seed, max rounds.
- **Run quality:** games attempted/completed/failed-early, wall-clock duration, and the totals behind every denominator.

### 2.5 The ledger — `evals/blunder-ledger.yaml` — **[Agent: python-backend]**

- New top-level `evals/` directory (with a short `evals/README.md` explaining the ledger contract: append-only, one YAML document per run, never rewrite history).
- Append one `---`-separated YAML document per run. **Write-only, hand-rendered YAML** (a small serializer for our known, flat-ish record shape) — avoids a PyYAML dependency for a format we only ever write; key order fixed for readable diffs. (If a reader/comparison tool lands later, *that* increment adds the parser dependency.)
- Record shape (illustrative, not exhaustive): `run` (date, duration, metrics_version), `code` (commit, branch, dirty), `provider` (name, models w/ digests or ids, server_version/note), `settings` (games, seed, max_rounds, resolved models), `quality` (attempted/completed/failed_early), `metrics` (each as `{rate, count, denominator}`).

### 2.6 Make target + docs — **[Agent: python-backend]**

- `make blunder-eval` → `uv run python -m graphia.tools.blunder_eval $(ARGS)`; added to `.PHONY` + the README's evals table (cost/live-model caveats per the existing rows' style).

---

## 3. Impact and Risk Analysis

- **Blast radius:** one new tool module + the shared instrument helper extraction (with `ollama_smoke` kept green), `evals/`, a make target, docs. **Zero production-code change**; the mocked suite never runs any of it.
- **Risk — message-template coupling.** Action-metric extraction parses our own announce/ballot templates; a template rewording breaks extraction. *Mitigation:* import the template constants as parse anchors; offline unit tests pin extraction against synthetic histories built from those same constants.
- **Risk — lexicon/heuristic drift.** The speech-based rules will get tuned. *Mitigation:* `METRICS_VERSION` bump discipline (a criterion, not a convention), rules as named constants beside the version.
- **Risk — denominator subtleties.** Peer-vote opportunities (ballots *on mafia targets* only) are easy to get wrong silently. *Mitigation:* denominators recorded next to every rate (functional-spec §2.1); unit-tested against hand-built game records.
- **Risk — ledger merge conflicts** if two runs land on diverging branches. Accepted: appends conflict trivially and resolve by keeping both documents.
- **Determinism posture unchanged** (architecture §6): mechanical seeds recorded for like-for-like reruns; LLM behavior stays non-reproducible — that's the thing being measured.

---

## 4. Testing Strategy

- **Offline unit tests — [Agent: testing]:** detectors over synthetic inputs (hand-built named `AIMessage` lists, players maps, message histories assembled from the real templates) covering every metric incl. denominator edge cases (no mafia ballots → peer-vote rate absent-not-zero); the provenance block with stubbed `git`/HTTP (dirty-warning text, digest extraction); the YAML renderer (append, `---` separation, stable key order); the CLI's provider forcing + env isolation. No network, no LLM; `safe_llm` untouched.
- **Live (the actual measurement) — manual, make-gated:** `make blunder-eval ARGS="--provider ollama --games N"` and `--provider bedrock` produce two comparable ledger records — the functional spec's own §2.2/§2.3 acceptance walk. This is the deliverable, not a CI step.
- No assertion anywhere depends on model text (architecture §6); the speech detectors are tested on synthetic text only.
