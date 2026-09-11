# ADR 014: Optimistic-Concurrency Whole-File Ledger Writes, over Strict Append-Only

- **ADR Number:** 014
- **Title:** Optimistic-Concurrency Whole-File Ledger Writes, over Strict Append-Only
- **Status:** Accepted
- **Date:** 2026-09-11
- **Authors:** Alexey Tigarev

---

## 1. Context

The quality ledger (`evals/blunder-ledger.yaml`) is the project's only long-term measurement history: one `---`-separated YAML document per measured run, written by `blunder_eval.append_record`, committed to the repository, read by `make view-ledger` and by hand. It is described as **append-only** in both `CLAUDE.md` and `evals/README.md`, and readers rely on that phrase.

**The trigger is [spec 043 — Partial Eval Runs](../spec/043-partial-eval-runs/functional-spec.md).** A record is appended exactly once, after the games loop finishes (`blunder_eval.py:4588`, reached only by falling off the end of the loop at `:4411`). So a run that is stopped contributes **nothing**, however many of its games played to a natural finish. Measured on 2026-09-10: three stopped runs, four completed games, **zero** records. The local-model arm needs about 33 minutes per game, so a ten-game run needs five and a half hours of uninterrupted machine time — the arm that costs nothing in money is the one that routinely loses hours of work. Spec 043's chosen mechanism is durability by having **already written**: the run's record is written after its first completed game and updated after each one thereafter, so an unobservable stop leaves the last written state standing.

That requires a run to modify its own document in a shared file, which strict append-only does not permit. Two further facts, both verified against the current code and the committed ledger, shape the decision:

- **The existing append is not crash-safe, and concurrent appends are not safe either.** `append_record` (`:3257`–`:3259`) opens the ledger in append mode and performs **two** writes — `"---\n"`, then the document — with **no `fsync`**. A kill between them leaves a separator with no document; a kill mid-document leaves a truncated one; a power cut can leave unflushed bytes. A multi-kilobyte buffered write is far above `PIPE_BUF`, so two runs appending to one ledger can interleave. Records 47 and 48 were written by concurrent runs on 2026-09-10 and survived only because they finished four minutes apart. **The corruption surface this ADR is often assumed to create already exists.**
- **The run identity is too weak to support self-modification.** `_run_id` (`:3280`) is a local timestamp to the **second**, so two runs launched in the same second produce the same id — not hypothetical, this project launches pairs four seconds apart.

Constraints the decision had to satisfy, all set by the author: results are saved **into the regular ledger** rather than a scratch file; graceful stop is **out of scope** (durability must not depend on catching a signal); concurrent runs must be safe, because running the provider arms at once is how a multi-arm baseline session works; and the check that marks a following run as measuring modified code is **left alone**, because a dirty next run is meaningful and the remedy is to commit or remove.

---

## 2. Alternatives Considered

### Alternative 1: Keep write-once-at-the-end (the status quo)

- **Pros:** Strictly append-only. No new machinery, no new failure modes to reason about. Every existing reader keeps working unchanged.
- **Cons:** A stopped run leaves nothing, which is the failure spec 043 exists to fix. It also keeps the unsynced two-write append described in §1, so a kill can truncate the tail document and concurrent writers can interleave. Choosing this leaves the five-and-a-half-hour local arm all-or-nothing and concurrent arms quietly unsafe.

### Alternative 2: Catch the stop and write once (signal handling)

- **Pros:** Append-only preserved, exactly one write per run, concurrency untouched. Reuses the existing scoring and summary path verbatim.
- **Cons:** Durability depends on the stop being catchable, and the stops actually observed here were not: `SIGKILL`, a background-task reaper, and a machine that slept while unattended. It needs two-stage handling or the first Ctrl-C stops responding for up to 33 minutes, leaving `SIGKILL` as the operator's only exit — the very failure being fixed. `signal.signal` raises off the main thread, and eight test modules drive `run_eval` in-process, so a handler or a module-level flag leaks between tests.

### Alternative 3: Byte-offset truncate-and-rewrite

- **Pros:** The simplest rewriting scheme. Preserves all earlier bytes exactly when a run is the sole writer, verifiable with a byte-prefix assertion.
- **Cons:** **Destructive under concurrency, and recorded here so it is not reached for again.** It remembers the file length before its first write and truncates back to that offset, which assumes a run's document is always *last* in the file. With two concurrent runs that assumption is false: run A truncating to its offset erases run B's document outright. Choosing it would mean forbidding concurrent runs on one ledger.

### Alternative 4: Pessimistic locking (`flock`) plus whole-file replace

- **Pros:** Closes the write window completely — no rollback is possible, and the reasoning is simpler than optimistic control. `flock` is released by the kernel when a process dies, so a killed run leaves no stale lock.
- **Cons:** Needs a **stable sidecar lock file**, because locking a file you then atomically replace does not work — the lock lives on the inode you are about to replace. That is one more artifact beside the ledger. It also serialises writers for collisions that are very rare at this cadence. **Retained as the recorded escalation** if conflicts are ever observed in practice.

### Alternative 5: Append each update, collapse them at the end

- **Pros:** Strictly append-only for the whole of a run — the safest possible behaviour during the vulnerable window, since an append cannot destroy prior bytes. The single rewriting operation happens once, at a calm moment.
- **Cons:** Mid-run the file holds several documents for one run, so **every existing reader that loads all documents counts that run several times** — including the ad-hoc `yaml.safe_load_all` scripts used routinely against this ledger. It changes what "a document" means for every consumer. A killed run leaves the duplicates permanently, uncollapsed. This is the option that keeps append-only and still fails.

### Alternative 6: One file per run — the ledger becomes a directory

- **Pros:** Removes contention **structurally** rather than managing it: each run owns its file exclusively, and atomically replacing a file only you write is trivially safe. No locking, no optimistic retries, no shared-file splicing. The most robust option on correctness alone.
- **Cons:** The largest blast radius of any option — every reader (`load_ledger`, the viewer, the tests, ad-hoc scripts), the migration of the 50 committed records, the loss of a single greppable committed artifact, and a rewrite of the `evals/README.md` contract. Rejected on cost, not on correctness, and **recorded as the direction if the ledger ever outgrows a flat file.**

### Alternative 7 (chosen): Optimistic concurrency control with retries, over a whole-file atomic replace

- **Pros:** Concurrency becomes **supported** rather than forbidden. No lock artifact exists for a killed run to leave behind — which matters when kills are the subject. Other runs' documents are carried across as **opaque text**, never parsed and re-emitted, so they stay byte-identical. Writes become **crash-atomic**, which they are not today, so the decision reduces the corruption surface. One document per run at all times, so the assumption every existing reader already makes holds unchanged.
- **Cons:** A residual rollback window remains (§5). Retry logic has to be right. The final write cannot self-heal and needs a fallback. And the splicing it depends on is a class of error that atomicity cannot protect against.

---

## 3. Decision

**Adopt Alternative 7.** Ledger writes become optimistic-concurrency-controlled whole-file atomic replacements, and the ledger stops being strictly append-only.

Each write renders its own document first, reads the ledger and hashes those bytes, splices its own document into the text in place of its previous one, re-checks the hash, and then replaces the **whole file** atomically — temp file in the same directory, `fsync`, `os.replace`, `fsync` the directory. It reads back to confirm its own document landed, and retries on conflict. An exhausted retry budget must never fail a measured run.

Two supporting changes are part of the decision rather than incidental to it: every record gains a **collision-proof `run.id`**, without which a run cannot reliably recognise its own document; and `--ledger-path` per arm ceases to be a safety requirement, becoming a convenience for throwaway runs.

**Alternative 4 (pessimistic locking) is retained as the named escalation**, to be taken if conflicts are ever observed rather than pre-emptively. **Alternative 6 (one file per run)** is the recorded direction if the flat file is outgrown.

---

## 4. Decision Rationale

**Primary rationale category: lowest operational risk**, which is not the reading the decision first invites. The instinct is that ending append-only trades safety for a feature. The measured position is the opposite: today's write is two unsynced appends that can truncate on a kill and interleave between concurrent writers, and the chosen design replaces both with a single atomic replacement. **The decision is a net reduction in corruption surface, and that is the main reason for it.**

Against **Alternative 1**, the status quo fails the requirement outright and carries the unsafety above; keeping it would mean accepting that the arm with the longest runs can never survive a stop.

Against **Alternative 2**, the deciding evidence is what actually killed runs here — `SIGKILL`, a reaper, and unattended sleep. Durability that depends on being told you are stopping does not cover them, and the two-stage handling it needs to avoid removing Ctrl-C is more delicate machinery than the write path it protects. The author ruled graceful stop out of scope, and the failure record supports that.

Against **Alternative 3**, the objection is decisive rather than a trade-off: it is destructive under exactly the usage pattern the project has (concurrent provider arms). It is recorded because it looks like the obvious implementation and is not.

Against **Alternative 4**, the trade is narrow and went on cost of artifacts, not on safety — locking is *strictly* safer. The judgement is that a sidecar lock file is a permanent cost paid against a collision probability that is very small at this write cadence (minutes apart on the cloud arms, half-hours on the local one), and that OCC's cheapness in the common case is worth the residual window **because that window is bounded and self-healing** (§5). It remains one line of code away if the assumption proves wrong, which is what makes accepting the risk reasonable rather than optimistic.

Against **Alternative 5**, the objection is that it exports its cost to every reader: an entry that appears several times mid-run silently double-counts in any tool that loads all documents, and the duplicates become permanent for the killed runs this spec is about. Preserving the append-only *word* while breaking the one-document-per-run *invariant* is the worse of the two trades.

Against **Alternative 6**, the rejection is purely cost. It is the most correct answer and it is written down as such, so the next person facing this does not have to rediscover it.

---

## 5. Decision Consequences

**Trade-offs accepted**

- **"Append-only" is no longer true, and both descriptions of it must change.** `CLAUDE.md` and `evals/README.md` say append-only and readers rely on it. The replacement property is narrower and must be stated: **a run contributes exactly one record, and that record is final once its run has ended.** Records belonging to ended runs are never touched again.
- **Nothing may assume a record is immutable — neither its bytes nor its values.** A record is only final once its run ended, so any future export, sync or analysis that treats a written record as permanent would be wrong. The subtler half is the **values**: every number in a record is computed over the games completed at the time of writing, so re-reading the same record later yields different numbers. Rates divide by a denominator that grows, and `_attach_ci` re-derives each Wilson band from that denominator, so the confidence intervals legitimately change at every update. A diff between two readings of one live record is not a finding.
- **A residual rollback window remains.** Two writers can both pass the hash check and then replace in sequence; the loser's newest update is regressed one generation. Its own read-back does not catch this — its document is intact; the *other* one regressed. Progressive updates **self-heal** on the victim's next write, so exposure is bounded to "a record is briefly one game stale". The **final** write cannot self-heal, having nothing after it: it retries harder, and on persistent failure writes its document to a named fallback file rather than losing the measurement.

**Gains, recorded as consequences rather than left implicit**

- **Writes become crash-atomic.** A reader or a kill sees the old complete file or the new one, never a partial document. This is a property the ledger does not have today.
- **Concurrent runs become supported.** `--ledger-path` per arm stops being a safety requirement. This matters because running the provider arms concurrently is exactly what a multi-arm baseline session does.

**Risks introduced**

- **A splicing bug is the one unrecoverable risk.** Splicing is the cut-swap-join the write depends on: cut the file's text at the separator lines into opaque pieces, swap the piece belonging to this run, join them back. A defect in it — a wrong boundary, the wrong piece swapped, a piece dropped, two records joined into one — produces well-formed but wrong content, and **atomicity is no defence, because wrong content is written just as atomically**. It is unrecoverable in a way other failures are not: this is the only long-term measurement history, and a measurement from months ago cannot be replayed. Verified today: 50 separators for 50 records, and the separator sequence appears nowhere else in the file. The mitigations live in [spec 043's technical considerations](../spec/043-partial-eval-runs/technical-considerations.md) rather than here.
- **Incomplete records become common, so readers must cope with one.** Today every record in the history belongs to a finished run. After this, a partial record is ordinary — which is why two display sites in `eval_ledger.py` come into scope in spec 043 despite presentation being excluded from it.

**Technical debt and future implications**

- **Re-scoring runs once per completed game instead of once per run**, and its cost is in three classes rather than one. Cheap: scalar folds, and the rates and Wilson bands re-derived from them. Linear in accumulated volume: everything computed from the accumulated *lists* of per-game values — `tally_outcomes(winners, scripted_sides, …)` for the outcome and win-rate tallies, and third-person self-talk over the pooled AI lines. **Superlinear: repetition alone**, which compares every pooled AI line against every other and is therefore quadratic in accumulated lines. Expected to be seconds against a 33-minute local game, but it must be **measured on a real ten-game run**, not assumed.
- **Every record gains a permanent `run.id`**, and new transcript directories carry its random suffix so the record-to-directory cross-reference keeps working. Older records have no `run.id`; nothing needs it retroactively, since the field exists for a live run to find its own document.
- **Partial runs leave uncommitted transcript directories more often**, and such a directory is what makes the *next* run record itself as measuring modified code. Per the author's ruling this is meaningful and stays — the remedy is to commit or remove — so the consequence is that the commit-or-remove discipline is exercised considerably more often than before.

**What would force this ADR to be revisited**

- **Observed OCC conflicts.** Any real conflict in practice is the signal to take Alternative 4 and add pessimistic locking on a gitignored sidecar.
- **The ledger outgrowing a flat file.** Growing record counts, or a need for genuinely parallel writers, points at Alternative 6 (one file per run) or a real datastore. That would supersede this ADR rather than amend it.

---

## 6. References

- Architecture: `context/product/architecture.md` — §5 Observability & Debugging (the local-mode logging surface), §3 Runtime & Execution Environment (the `.graphia/` local footprint)
- Related ADRs: **011** (Ablatable Gameplay Feature Flags — its display-only/non-gameplay exemption is why this change carries no ablation flag)
- Related CRs: _none_
- Related specs: `context/spec/043-partial-eval-runs/` — the triggering spec; its technical considerations carry the write cycle, the splicing obligations and the test plan
- Ledger contract: `evals/README.md` — the document to amend where it describes the history as append-only
- Code: `src/graphia/tools/blunder_eval.py` — `append_record` (`:3236`–`:3260`), `_run_id` (`:3280`), the games loop (`:4151`–`:4411`), the post-loop scoring (`:4413`–`:4587`), the single append call site (`:4588`)
