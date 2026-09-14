# Functional Specification: Keeping What a Stopped Measurement Run Produced

- **Roadmap Item:** _None — tooling._ This is the quality-measurement layer, not the game. It adds no player-facing capability, so the roadmap is deliberately left untouched, following spec 041's convention. The request has been logged since **2026-06-22** in `context/product/backlog.md` as *"Display partially-complete eval runs"*, which deliberately left the sense of it open; this spec resolves that to one of the three senses and records the other two as out-of-scope.
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

A measured run plays a chosen number of games against a chosen AI provider, scores how well the AI played, and adds **one entry** to the project's tracked quality history — the record that lets today's AI behaviour be compared against last month's.

**Today that entry appears only if the run plays every game it was asked for.** A run that ends early — the person stops it, something kills it, it crashes, the machine loses power — contributes **nothing at all**, however many of its games played through to a natural finish. The games are gone as measurements even though they were played, scored and paid for.

**This is measured, not hypothetical.** On 2026-09-10 three runs were stopped part-way. Between them **four games played to completion, and the tracked history gained zero entries.** One of those four was the only game of the whole exercise played against the local model.

**The case that most needs this is the case least able to survive without it.** The local-model arm takes about 33 minutes per game, so a ten-game run needs five and a half hours of uninterrupted machine time. The cloud arms finish ten games in about forty minutes. So the arm that costs nothing in money is the one where hours of work are routinely lost, and the longer a run is, the more certain it is to lose everything.

**The desired outcome:** a run that stops early leaves behind what it actually achieved, clearly labelled as short of what it set out to do, so it can be read and weighed without ever being mistaken for a planned run of that size.

**How success is measured:** a run asked for ten games and stopped after four leaves one entry whose numbers cover exactly those four games, and a person reading the history can tell at a glance both that it ended early and how far short it fell — without asking anyone or reading anything else.

---

## 2. Functional Requirements (The "What")

- **A run that stops early still leaves an entry for the games that finished.**
  - Whatever ends the run — the person stopping it, something killing it, a crash, the power going — the games that played through to a natural finish are kept and scored, and they land in the tracked quality history like any other result. A run that finishes everything it asked for behaves exactly as it does today.
  - **Acceptance Criteria:**
    - [x] Given a run asked for ten games that is stopped after four have finished, when the person looks at the tracked quality history, then there is one entry for that run, and its scores and counts cover exactly those four games.
    - [x] Given a run that is stopped before any game has finished, when the person looks at the history, then no entry for it has been added — there was nothing finished to record.
    - [x] Given a run that plays every game it was asked for, when the person looks at its entry, then it reads exactly as such entries read today — nothing about a complete run changes.
    - [x] Given a run stopped part-way, when the person reads the entry, then it states which AI provider was measured, and the same supporting details every other entry carries.

- **The entry says that it ended early, and by how much.**
  - An entry from a stopped run is never silently shorter than it should have been. It states both numbers — how many games were asked for and how many finished — so a reader can judge the weight of the result themselves rather than inferring it.
  - **Acceptance Criteria:**
    - [x] Given an entry from a run that ended early, when the person reads it, then it says the run ended early.
    - [x] Given that same entry, when the person reads it, then it states how many games were asked for **and** how many finished.
    - [x] Given an entry from a run stopped after four of ten games, and an entry from a run that asked for four games and played all four, when the person compares them, then the two are clearly distinguishable as an incomplete run and a complete one.

- **The entry is brought up to date as the run goes, so a stopped run has already been recorded.**
  - As each game finishes, the run's single entry is updated to cover everything that has finished so far, with every count and score computed over exactly those games. Nothing has to happen at the moment a run stops — by the time it stops, the entry already says where it got to. That is what lets it survive a stop the run never sees coming, including a power cut or an abrupt kill.
  - **Acceptance Criteria:**
    - [x] Given a run asked for ten games, when its first game finishes, then an entry for that run exists in the tracked history and covers exactly that one game.
    - [x] Given that same run, when its fourth game finishes, then that **same single entry** now covers four games — a ten-game run leaves one entry, never one per game.
    - [x] Given a run whose fourth game has finished and which is then killed with no warning, when the person looks at the history, then the entry stands exactly as it was written after the fourth game.
    - [x] Given a run killed before its first game finished, when the person looks at the history, then no entry for it exists at all.
    - [x] Given a run that plays every game it was asked for, when the person reads its entry, then it is indistinguishable from an entry written the old way — the same numbers over the same games.

    > **AMENDED before implementation (2026-09-10), at the author's direction.** This requirement previously read *"The person is told what was kept, at the moment the run stops"*, and asked for the usual end-of-run summary to be printed as a run stopped. That is unimplementable under the mechanism the author chose: catching a stop gracefully is **out of scope**, and a run that is killed cannot print anything — the process is already gone. Saving progress as the run goes achieves the same end by a different route, and a stronger one: it survives stops that no amount of graceful handling could catch.

- **Keeping results as the run goes must not cost a clean run its clean record.**
  - Every entry records whether the code it measured was unmodified when the run **started** — that is what makes two entries comparable. That judgement is made once, before the first game, and updating the entry as the run goes must never revisit it. Saving the first game's results is itself a change to the project, so a run that re-checked would libel itself as measuring modified code from its second game onward.
  - **Acceptance Criteria:**
    - [x] Given a run started from an unmodified checkout, when its entry is read after four games have finished, then the entry still records the code as unmodified.
    - [x] Given that same run, when its entry is compared after its first game and after its fourth, then the recorded clean-or-modified mark is identical in both — updating an entry never re-decides it.
    - [x] Given a run started from a modified checkout, when its entry is read, then it records the code as modified, exactly as today.

    > **CLARIFIED before implementation (2026-09-10), at the author's direction.** This requirement previously also asked that saving progress create no fresh reason for the *next* run to be recorded as measuring modified code. **The author's ruling is the opposite, and it is deliberate: a following run reading as modified is meaningful and actionable — remove or commit the previous run's results to get a clean one.** So the check that decides clean-or-modified is left exactly as it is, and the discipline it enforces is a feature. What this requirement now protects is only the narrower and unarguable case: a run must not talk itself into being modified by its own saving.

- **A game that fails inside an otherwise complete run keeps behaving as it does today.**
  - The backlog request covered two situations. This one already works: a run whose individual games fail carries on to the end, counts the failures, and produces its entry. It is stated here so that the change above is not allowed to disturb it.
  - **Acceptance Criteria:**
    - [x] Given a run of ten games in which two games fail but the run reaches its end, when the person looks at the history, then there is one entry that counts eight finished and two failed, exactly as today.
    - [x] Given that entry, when the person reads it, then it is **not** marked as having ended early — it did not; it finished everything it was asked to attempt.

---

## 3. Scope and Boundaries

### In-Scope

- Keeping and scoring the games that finished when a measured run stops before playing all of them.
- Marking such an entry as incomplete, carrying both the number of games asked for and the number that finished.
- Bringing the entry up to date as each game finishes, so that by the time a run stops it has already been recorded.
- Telling the person plainly, on a run that reaches its own end, when nothing could be kept because no game finished.
- Preserving the honesty of the "was the code unmodified" mark for the stopped run.
- Leaving complete runs, and runs with individually failing games, exactly as they behave today.

> **RECONCILED with the two notes above (2026-09-11), at the author's direction.** Three bullets in this list still described the spec as it read *before* those amendments, and contradicted them. **"Printing the usual end-of-run summary, plus the early-stop statement, at the moment a run stops" is gone** — it is unimplementable under the mechanism the author chose, and the boundary it crossed now sits in Out-of-Scope as *"Catching a stop gracefully"*; what replaces it is the saving-as-you-go that reaches the same end by a different route, and survives stops no graceful handling could catch. **"Telling the person plainly when nothing could be kept" is narrowed to a run that reaches its own end**, for the same reason: a killed process prints nothing. And **the clean-code mark is protected for the stopped run only** — the author's ruling is that the *next* run reading as modified is meaningful and actionable, which Out-of-Scope already states outright. No requirement and no acceptance criterion changes; this is the scope list catching up to decisions already recorded above.

### Out-of-Scope

- **Showing incomplete runs specially in the ledger viewer.** One of the three senses the backlog left open, and deliberately not chosen here: the entry will say what it needs to say, and how the viewer presents it is separate work.
- **Showing progress while a run is still going.** The other sense the backlog left open. Useful for the five-hour local-model runs, but a different capability from keeping what finished.
- **Resuming a stopped run.** Nothing here lets a run pick up where it left off; a stopped run is finished, just shorter than intended.
- **Rescuing runs that were already stopped.** The four games stranded on 2026-09-10 stay stranded. Their game logs are readable but were never scored, and nothing here reaches back to score them.
- **Catching a stop gracefully.** Nothing here notices that it is being stopped, negotiates a tidy shutdown, or finishes the game in flight first. The author's ruling: graceful stop is out of scope. Durability comes from having already saved, not from reacting to the stop — which is why an abrupt kill and a power cut are covered rather than excluded.
- **Changing what counts as a modified project for the *next* run.** A following run recording itself as measuring modified code, because the previous run's results are sitting unsaved to version control, is **correct and useful** — the remedy is to remove or commit them. This spec does not soften that check.
- **Changing what is measured, or how any score is calculated.** No metric changes; a partial entry holds the same kinds of numbers over fewer games.
- **All other roadmap items**, which are gameplay rather than measurement and are addressed in their own specifications: **Asynchronous Day Chat** (Rate-Limited Concurrent AI Chatter, Concurrent Human Typing with Live Display, Vote-Opens-Lock-Chat Handoff), **AI Tool-Use Demonstration** (Day-Phase Investigation Tool, Day-Phase Evidence-Builder Tool, Visible Tool-Call Beats in Chat, Moderator Helper Tools), **Evidence Citation** (Day-Phase Evidence Tool), **Expanded Role Roster** (Detective Role, Protector Role, Role-Mix Configuration), and **LLM-as-Judge Game-Quality Evaluation** (Whole-Transcript Judge).

---

## 4. Verification

**VERIFIED 2026-09-14.** All 17 acceptance criteria above are marked from evidence, not from inspection. Three independent kinds of evidence were used, because this spec's central promise — that a record survives an event the process never sees — is not expressible in a test suite.

**The mocked suite: 283 tests across the seven spec-043 modules** (`test_ledger_write.py`, `test_run_identity.py`, `test_progressive_ledger_write.py`, `test_early_stop_marker.py`, `test_partial_run_viewer.py`, `test_finalise_cost.py`, `test_spec043_cluster_skip.py`), inside a whole-project suite of **3141 passed / 3 skipped**, up from 2845 when the spec began. Every load-bearing assertion was mutation-checked — the implementation broken, the specific test watched go red, the source restored — which caught two live bugs no integration test could see: a missing `not raw` guard that made the *first* write to a fresh ledger prepend a blank line for the file's life while everything still parsed, and a pass-through comparison that silently stopped protecting the one part of the file no run owns.

**Three real measured runs, on real models:**

- **A real `SIGKILL`** of a three-game run, 47 ms after its first record appeared — criterion *"killed with no warning … the entry stands exactly as it was written"*. One intact document survived, carrying `ended_early: true`, `settings.games: 3`, `seq: 1`. The kill landed *inside game 2*, so `games_attempted` was 2 in memory while the record said 1: the balance invariant held **structurally**, not by luck. A 4 Hz poller recorded exactly one state change, absent → complete; the file was never observed partial.
- **Record 51** — three `ollama` games, 3774.873 s. A poller captured all four writes: `seq` 1→2→3→4, pool 105→201→264, **one document throughout**, and `ended_early` **dropping at seq 3** — the last *progressive* write, not the final one, because the marker is re-derived every pass. So a live record never claims a run fell short once it has not.
- **Record 52** — ten `bedrock` Nova games, 2969.058 s, eleven writes, one document.

**The invariant the suite can only assert on fixtures, asserted against real history.** After record 52's **eleven** whole-file rewrites, `git diff --numstat` reports **0 deletions** on `evals/blunder-ledger.yaml`: all 51 prior records byte-identical across 171,686 bytes of curated data. A splice that reformatted any of them — a float rendering, a hand-written `notes` block scalar, key order — would have shown deletions.

**One nuance recorded rather than glossed, against the criterion *"nothing about a complete run changes"*.** A complete run's *numbers* are unchanged — scores, counts, provider, settings and their order all read as before, and the suite pins a complete run's record as differing from a one-shot render in the `run.seq` line alone. But every record, complete ones included, now additionally carries **`run.id`** and **`run.seq`**, which did not exist before this spec. These are additive fields that no existing reader parses positionally and that technical-considerations §2.3 (What `metrics_version` is for, and why this is not a bump) explicitly holds not to be a metrics bump; `METRICS_VERSION` stays at 1. The criterion is judged met in substance — a reader's reading of a complete entry does not change — but the two new fields are named here so the judgement is auditable rather than implied.

**What remains unverified, stated rather than implied.** No test sends a real signal and none can express a power cut; durability is a property of having already written to disk, and the honest verification of it is the manual kill above. And the 3.15× in-situ slowdown diagnosed in technical-considerations §2.8 (Cost of re-scoring, and the one number to measure) remains a *leading* diagnosis — memory pressure — not a settled one; settling it needs one repeat ten-game Nova run on a machine that has not just hosted a 30B local model.
