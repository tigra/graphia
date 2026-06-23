# Functional Specification: Continuous Persona-Similarity Metrics (Average + Peak)

- **Roadmap Item:** Eval-measurement refinement — direct follow-up to **spec 031 (Distinct AI Personas Across the Roster)**, whose persona-distinctiveness measure proved too coarse. Relates to the **persona-realism** and **lexical → semantic** measurement threads. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Spec 031 added a persona-distinctiveness measure to every measured (eval) run: a **count of how many pairs of AI characters are near-*identical*** to each other. The bar for "near-identical" is deliberately high (essentially near-verbatim wording). In practice the generated characters almost never clear that bar — they carry different names, jobs, and backstories — so the count sits at **zero on virtually every run** (a backfill across the whole recorded history found it `0` everywhere except a single run where a few characters collapsed to the same template). A number that is almost always zero tells a reviewer very little: it can flag a blatant copy-paste, but it **cannot distinguish a cast that is subtly alike (all the same calm, observant temperament) from one that is genuinely varied.**

This change adds **two continuous companion measures** over all pairs of a run's AI characters, so distinctness becomes legible rather than a stuck zero:

- **Average similarity** — how alike the cast is *overall*. It moves smoothly from "no two are alike" to "identical" and varies meaningfully run-to-run, so a reviewer can compare casts even when no two characters are near-duplicates.
- **Peak similarity** — the *most-alike pair* in the run. It is the continuous generalization of the near-duplicate count: when no pair is near-identical the count is zero but the peak still tells you *how close the closest pair got* (e.g. 0.6 vs 0.3), and when a few characters **collapse** to the same template the peak reaches the top of its range and flags it — the exact case the average smooths over (in the one collapsed run, the average stayed low (~0.07) yet the peak hit the maximum).

Together with the spec-031 count, the three give the full picture: *are any pairs near-copies?* (count), *how alike is the cast overall?* (average), *how alike is the closest pair?* (peak). All three **complement** each other; none is replaced.

It is **pure measurement** — nothing about how a game plays changes; this only adds recorded numbers and shows them. To make them comparable across the project's history, **past runs whose full game transcripts were preserved are backfilled** (computed from those preserved characters); older runs without preserved transcripts simply lack them, as they already lack the spec-031 count.

**Success looks like:** every measured run reports the average and peak persona similarity alongside the near-duplicate count; both are visible in the ledger viewer with their own labels; past transcript-preserved runs carry them too; the average takes a range of values across runs (not stuck at zero), and the peak flags any run where characters collapsed.

---

## 2. Functional Requirements (The "What")

- **Each measured run records the average and the peak similarity of its AI characters.**
  - Alongside the existing near-duplicate count, every run records two numbers over all pairs of its AI characters' table-facing descriptions: the **average** similarity across the pairs and the **peak** (most-similar-pair) similarity — each ranging from "completely different" to "identical."
  - **Acceptance Criteria:**
    - [ ] Given a completed measured run with at least two AI characters, when its recorded result is read, then it includes both the average and the peak persona-similarity, alongside the existing near-duplicate count.
    - [ ] Given a run (or game) with fewer than two AI characters — no pair to compare — when its result is read, then the average and peak are reported as not-applicable (blank), not a misleading zero.

- **The average and peak are continuous companions to the near-duplicate count — all are kept.**
  - The near-duplicate count from spec 031 remains; the average and peak are added beside it, reading as: *how many pairs are near-copies* (count), *how alike the whole cast is* (average), and *how alike the closest pair is* (peak).
  - **Acceptance Criteria:**
    - [ ] Given any run that records persona measures, when it is read, then the near-duplicate count, the average similarity, and the peak similarity are **all present**.
    - [ ] Given a varied cast (no near-identical pairs, so the count is zero), when read, then the average shows a meaningful low value **and** the peak shows the closest-pair value (e.g. well below "identical") — graded distinctness the zero count alone would hide.
    - [ ] Given a run where a few characters collapsed to near-identical descriptions, when read, then the **peak** reaches the top of its range (flagging the collapse) even if the average stays low.

- **Both are visible in the ledger viewer.**
  - The average and the peak each appear in the viewer's run list and in a run's detail view, with their own labels, clearly distinct from each other and from the near-duplicate count. Runs that lack them render blank rather than erroring.
  - **Acceptance Criteria:**
    - [ ] Given the ledger viewer, when the reviewer reads a run that has them, then the average and the peak are each shown with their own distinct label (not conflated with each other or with the near-duplicate count).
    - [ ] Given a mix of runs where some lack these numbers, when the reviewer scrolls the list, then those rows render blank in those columns without error and the layout stays readable.

- **Past transcript-preserved runs are backfilled.**
  - Runs whose complete game transcripts were preserved are filled in with the average and peak (computed from those preserved characters), so the numbers are comparable across the recorded history. Runs without preserved transcripts are left without them.
  - **Acceptance Criteria:**
    - [ ] Given a past run whose transcripts were preserved, when the ledger is read after this change, then that run shows an average and a peak persona-similarity consistent with its preserved characters.
    - [ ] Given a past run with no preserved transcripts, when the ledger is read, then it simply shows no average or peak (blank), without error.
    - [ ] Given a run that already recorded persona measures live, when its average and peak are computed from its preserved transcripts, then the results match a fresh measurement of that run (the backfill is faithful, not an approximation) — and the backfill changes nothing else in any record.

- **Pure measurement — no change to how a game plays.**
  - Adding, recording, and displaying these numbers changes nothing about gameplay, outcomes, the characters generated, or any other recorded value.
  - **Acceptance Criteria:**
    - [ ] Given the same games, when they are played with and without these numbers recorded, then the game outcomes themselves are identical — only the recorded result gains the new numbers.

---

## 3. Scope and Boundaries

### In-Scope

- Recording, per measured run, the **average** and the **peak** pairwise similarity of its AI characters' table-facing descriptions, alongside (not replacing) the spec-031 near-duplicate count.
- Surfacing both in the ledger viewer (run list **and** per-record detail) with their own labels, and reading older records that lack them without error.
- **Backfilling** the runs whose full transcripts were preserved, computed faithfully from those transcripts (validated to match a live measurement), as a purely additive edit to those records.

### Out-of-Scope

- **Replacing or removing** the spec-031 near-duplicate count — it stays; this is additive.
- A **semantic / meaning-based** similarity measure (embeddings or an AI judge) that would capture the deeper "all the characters *feel* the same archetype" sameness — this stays a **text-level** measure, like the existing count; the semantic measure is a separate backlog item.
- **Further distribution** statistics beyond the average and peak (percentiles, spread/variance, full histograms) — a possible later refinement.
- Backfilling runs that have **no preserved transcripts** (there is nothing to compute from).
- Any change to **persona generation, game rules, win conditions, or other recorded metrics**.
- All other roadmap items, which are automatically out-of-scope for this specification.
