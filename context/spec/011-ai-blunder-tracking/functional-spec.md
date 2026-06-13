# Functional Specification: AI Blunder Tracking (Repo-Persisted Quality Ledger)

- **Roadmap Item:** Not a roadmap feature — a quality-measurement increment in the spirit of the Day-phase integrity trio (007–009) and the dialogue evals they produced, requested ad hoc. (Roadmap order unaffected; Phase 5 remains next.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Play-testing keeps surfacing AI behaviors that break the social-deduction illusion. Repetition ("parroting") was the first, and spec 009 built the measurement that fixed it. But there's a whole family of **self-consistency blunders** the AI players commit that nothing currently watches:

- an AI **votes against itself** — tries to start a vote on itself, or approves its own execution;
- a Mafioso **helps execute a fellow Mafioso** — starting a vote against a teammate, or voting Yes on one;
- an AI **talks about itself in the third person**, referring to itself by name as if it were another player at the table.

Each of these is jarring to a human player and, today, invisible to the maintainer except by luck of observation during play. Worse, the project now runs on **two different AI providers** (the cloud model and the local Ollama models), and a behavior that's rare on one can be common on the other — there is no way to know without measuring both.

The deeper gap is structural: every existing quality measurement prints a one-off report to the terminal and is gone. There is **no durable record** of how the AI behaved at any point in the project's history — no way to ask "did the last prompt change make third-person self-talk worse on the local model?".

**Desired outcome:** one quality-measurement run plays a batch of games against a chosen provider/model, counts every watched behavior (the new blunders **plus** the existing repetition measure, folded into one family), and **appends a dated record to a ledger kept in the repository** — so AI quality becomes a tracked, comparable, history-backed property of the project rather than an anecdote. A "baby MLOps" loop: measure → commit the record → change something → measure again → compare by reading two records.

**Success is measured by:** after running the measurement against both providers, the repository contains records showing, for each run: when it ran, which provider and model(s), which version of the game, how many games were played, and the rate of every watched behavior — and a maintainer can answer "how does the local model compare to the cloud model on self-votes?" by reading the ledger alone.

---

## 2. Functional Requirements (The "What")

### 2.1 The watched behaviors are defined and counted

- **As the** maintainer, **I want** each blunder defined precisely enough to count, **so that** rates are comparable across runs and models.
  - **Acceptance Criteria:**
    - [ ] **Self-vote** is counted as **two separate measures** — an AI **initiating** a vote against itself, and an AI casting a **Yes ballot** on its own execution. The initiation counts even when the game's own safety nets quietly absorb it (the AI's own turn-handler rejects a self-targeted vote) — the measurement sees the attempt, not just the survivors.
    - [ ] **Mafioso peer-vote** is counted as **two separate measures** — a Mafioso **initiating** a vote against a fellow Mafioso, and a Mafioso casting a **Yes ballot** on a fellow Mafioso's execution (tracked as signals, not forbidden by the game).
    - [ ] Vote-**initiations** and Yes-**ballots** are always kept as **distinct** measures (never collapsed into one rate): they are different tells with different natural denominators, and a later fix might target one without the other.
    - [ ] **Third-person self-talk** is counted when an AI's spoken line refers to itself by its own name as though it were another player (regardless of whether the content is accusatory). *(Self-accusation — naming oneself as the suspect — was considered and **dropped**: detecting it reliably needs a suspicion-keyword lexicon, too fragile to compare across runs and models. Third-person self-talk needs only the speaker's own name and is the robust signal kept.)*
    - [ ] **Repetition** (the spec-009 near-duplicate measure) is reported alongside the behaviour measures as part of the same family — one run yields one set of numbers covering them all.
    - [ ] Each behaviour is reported as a **rate** with its denominator visible (e.g. self-vote initiations per AI vote-initiation; peer Yes-ballots per Mafioso ballot cast on a Mafia target; third-person lines per AI spoken line), not as a bare count, so runs of different sizes compare honestly.
    - [ ] Text-based detection (third-person self-talk) is **approximate by nature**; the spec accepts detection that may miss rephrasings or over-count edge cases, as long as the rule applied is consistent across runs and documented next to the numbers. Action-based detection (self-vote, peer-vote — both facets) is exact.

### 2.2 One run measures a chosen provider; both providers are covered

- **As the** maintainer, **I want** the same measurement to run against the cloud model and against the local Ollama models, **so that** quality is known per provider, not assumed.
  - **Acceptance Criteria:**
    - [ ] A measurement run targets a chosen provider — the cloud (Nova) gameplay model or the local (Ollama) verified model pair — plays a configurable number of games unattended, and produces the full set of behavior rates for that provider.
    - [ ] Running it once per provider yields **directly comparable records**: same behaviors, same definitions, same rate denominators, differing only in the provider/model identification.
    - [ ] The run reaches a real model (it measures real behavior), so it lives alongside the project's other live evaluations — invoked deliberately, never as part of the ordinary offline test suite, with real cost/time expectations stated where it's documented.

### 2.3 Every run appends a record to a repo-kept ledger

- **As the** maintainer, **I want** each run's results persisted in the repository, **so that** AI quality has a history I can diff, not a terminal scroll-back I lost.
  - **Acceptance Criteria:**
    - [ ] Each completed run **appends one record** to a ledger that lives **inside the repository** (committed alongside the code, human-readable).
    - [ ] **The state of the code that produced a run is identifiable from its record.** A record carries the exact code version (the commit identifier) and a **clean/dirty flag**: whether the working copy contained changes not yet recorded in the project history at run time. Since prompts, detection rules, and settings all live in the code, a clean record is fully attributable to its commit.
    - [ ] Given the working copy has unrecorded local changes, when a measurement run starts, then the maintainer is **warned up front** that the results will not be attributable to any recorded version — the run proceeds (iterating before committing is normal), but its ledger record is unmistakably marked as coming from a modified working copy.
    - [ ] **The models are identified by more than their names.** For local (Ollama) runs the record carries each model's **content fingerprint (digest)** — a re-pulled tag with silently changed weights is distinguishable — plus the local server's version. For cloud runs the record carries the full model identifier; the record acknowledges that provider-side model updates are invisible and the run date is the only proxy.
    - [ ] A record carries the **effective settings actually used** — the resolved model names (after any environment overrides), the number of games, and the structural seed(s) — so a run can be repeated like-for-like.
    - [ ] A record carries a **metric-definitions version**, bumped whenever a detection rule changes, so rates measured under different rules are visibly incomparable in the ledger itself.
    - [ ] A record carries **run-quality counts** — games attempted, completed, and failed/ended early, plus the run's duration — alongside the date, provider, the totals behind each denominator, and the rate of every watched behavior, so a degenerate run cannot masquerade as a clean baseline.
    - [ ] Records **accumulate** — a new run never overwrites or rewrites history; the ledger reads chronologically.
    - [ ] A maintainer can answer "Nova vs Ollama on behavior X" or "before vs after prompt change Y" by reading the ledger alone — no re-running, no external service, no tooling beyond a text editor. (A comparison/reporting command is explicitly **not** part of this increment.)

### 2.4 Measurement only — the game itself is unchanged

- **As a** player, **I want** the game to play exactly as it does today, **so that** this increment changes what the maintainer *knows*, not what the game *does*.
  - **Acceptance Criteria:**
    - [ ] No gameplay change: no new prompts, rules, guards, or on-screen elements. The watched behaviors remain *possible* in play; this increment only counts them.
    - [ ] Fixes for whatever the measurements reveal (prompt nudges, mechanical guards) are **explicitly out of scope** — each becomes its own later, evidence-driven change, measured against the baseline this increment establishes.

---

## 3. Scope and Boundaries

### In-Scope

- Precise, countable definitions of the new blunders — **self-vote** and **Mafioso peer-vote**, each split into a *vote-initiation* and a *Yes-ballot* measure, plus **third-person self-talk** — and folding the existing **repetition** measure into the same reported family. (Self-accusation was considered and dropped as too fragile to detect by keyword.)
- A measurement run that plays a batch of real games against a **chosen provider** — cloud (Nova) or local (Ollama, the verified pair) — and produces the full rate set; both providers covered by running it per provider.
- A **repo-committed, append-only, human-readable ledger** of run records (date, provider/models, game version, volumes, rates).
- Counting **attempts** the game's safety nets absorb (e.g. a rejected self-vote initiation), not just visible outcomes.

### Out-of-Scope

- **Any fix or mitigation** for the measured behaviors — no prompt changes, no new mechanical guards (measure first; fixes are later specs).
- **Comparison/report tooling, dashboards, charts, or threshold gates** — the ledger is the deliverable; humans read it. (A comparison command or regression gate can be a later increment.)
- **Automatic/scheduled runs (CI)** — runs are manual and deliberate, like the project's other live evaluations.
- **Human-player behavior** — only AI players are measured.
- **Perfect text-based detection** — approximate, consistently-applied detection is accepted for the one speech-based behaviour (third-person self-talk).
- **Recording hardware/OS details or dependency versions** beyond what the code version already pins — hardware affects speed, not the watched behaviors, and dependencies are pinned by the committed lockfile (which the clean/dirty flag guards).
- **Other roadmap items** (Phase 5 Setup Flexibility & Richer Night Resolution; Phase 6 Personas & Async Day Chat; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
