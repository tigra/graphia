# Functional Specification: Semantic (Meaning-Based) Persona Similarity

- **Roadmap Item:** Eval-measurement — the **"lexical → semantic" measurement** backlog item graduating to a spec; the meaning-based counterpart to the *text-level* persona measures (**spec 031** near-duplicate count, **spec 032** average + peak). Relates to the **persona-realism** thread. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

> **Shared surface with spec 032 (in flight):** 032 and 033 both add a persona-similarity measure, a ledger-viewer column, and a backfill of transcript-preserved runs. They are complementary, not conflicting, but they touch the same areas — implement with that in mind (sequentially, or as one coordinated change), not as blind parallel edits.

---

## 1. Overview and Rationale (The "Why")

The persona measures so far judge characters by their **words**: spec 031 counts near-*identical* pairs, and spec 032 adds the average and peak word-level similarity. A backfill across the whole recorded history showed they all report the cast as **distinct** — because the generated characters carry different names, jobs, and backstories. Yet reviewers consistently see the opposite: the cast feels **samey**, because nearly every character is the same *kind* of person — calm, observant, even-tempered. That sameness lives in **meaning** (temperament, how the character would behave), not in wording, so a word-level measure is structurally blind to it. This is the gap every prior persona measure has failed to close.

This change adds a **meaning-based** similarity measure: it judges how alike the characters are in **kind** — personality, temperament, the sort of person they are — rather than in phrasing. Two characters written in completely different words but both "a quiet, watchful townsperson who weighs their words" read as **similar** on this measure, exactly where the word-level measures read them as distinct. This is the measure that can finally tell a genuinely varied cast from a uniform one.

Because meaning can't be assessed by a word-matching rule, this measure relies on a **model that understands meaning** — so, unlike the free, instant, fully-local text-level measures, it **needs that model to compute** (with the cloud access and credentials the model runs on, on every run, including otherwise-local ones). Unlike a free-form judgment, though, it is **reproducible**: the same characters always yield the same number. Whether it actually captures the sameness reviewers perceive is itself an open question this lets us test: under the project's **effort-not-results** principle ([CR 005](../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)), we record it and check whether archetype-samey casts score **higher** on it than on the word-level measures — a confirmed *or* refuted result both being complete.

It **complements** (does not replace) the text-level measures; all are kept and read together. To make it comparable across the project's history, **past runs whose full transcripts were preserved are backfilled** with it; runs without preserved transcripts are left without it.

**Success looks like:** every measured run records a meaning-based persona similarity alongside the text-level measures; it is visible in the ledger viewer with its own label; transcript-preserved past runs carry it too; and — the test — on casts reviewers judge samey it reads **notably higher** than the word-level average, confirming (or refuting) that it surfaces the sameness the word-level measures miss.

---

## 2. Functional Requirements (The "What")

- **Each measured run records a meaning-based persona similarity.**
  - Every run records a number capturing how alike its AI characters are in **personality / temperament / kind of person** — judged by meaning, not wording — alongside the existing text-level measures.
  - **Acceptance Criteria:**
    - [ ] Given a completed measured run with at least two AI characters, when its recorded result is read, then it includes a meaning-based persona-similarity number, alongside the text-level measures (spec 031 / 032).
    - [ ] Given a run with fewer than two AI characters — no pair to compare — when its result is read, then the meaning-based similarity is reported as not-applicable (blank), not a misleading zero.

- **The measure captures sameness the word-level measures miss (validated, effort-not-results).**
  - The point of the measure is to rate two differently-worded but same-temperament characters as similar. Whether it does — rating archetype-samey casts higher than the word-level measures do — is measured against the recorded history, not assumed.
  - **Acceptance Criteria:**
    - [ ] Given a meaning-based similarity recorded for differently-worded but same-temperament characters, when it is read, then it is **high** (they are judged alike), whereas the word-level average for the same characters is low.
    - [ ] Given a measured set of runs, when the meaning-based similarity is compared with the word-level average on the same runs, then the comparison is recorded and the hypothesis — *the meaning-based measure rates archetype-samey casts higher than the word-level measure does* — is logged **confirmed or refuted**, either being a complete result.

- **It is model-dependent — it relies on a meaning model, so it is not free, but it is reproducible.**
  - Unlike the instant, fully-local text-level measures, this one needs a model to assess meaning, so producing it takes real compute and the cloud access/credentials that model runs on — on every run, including otherwise-local ones. But for the same characters it always yields the **same** number (it is not a varying free-form judgment); this reproducibility, plus the compute/cloud cost, is the accepted trade for measuring meaning.
  - **Acceptance Criteria:**
    - [ ] Given the same characters, when the meaning-based measure is computed twice, then it yields the same number (it is reproducible, not a varying judgment).
    - [ ] Given the meaning-based measure beside the text-level measures, when they are presented, then it is identifiable as the model-computed semantic measure, distinct from the exact word-level counts.

- **It is visible in the ledger viewer.**
  - The meaning-based similarity appears in the viewer's run list and in a run's detail view, with its own label, distinct from the text-level measures. Runs that lack it render blank rather than erroring.
  - **Acceptance Criteria:**
    - [ ] Given the ledger viewer, when the reviewer reads a run that has it, then the meaning-based similarity is shown with its own distinct label, separate from the text-level measures.
    - [ ] Given a mix of runs where some lack it, when the reviewer scrolls the list, then those rows render blank in that column without error and the layout stays readable.

- **Past transcript-preserved runs are backfilled.**
  - Runs whose complete game transcripts were preserved are filled in with the meaning-based similarity (computed from those preserved characters), so it is comparable across the recorded history. Runs without preserved transcripts are left without it.
  - **Acceptance Criteria:**
    - [ ] Given a past run whose transcripts were preserved, when the ledger is read after this change, then that run shows a meaning-based similarity computed from its preserved characters.
    - [ ] Given a past run with no preserved transcripts, when the ledger is read, then it simply shows nothing for this measure (blank), without error.
    - [ ] Given the backfill, when it runs, then it only adds this measure to the eligible records and changes nothing else.

- **Complements the text-level measures — pure measurement, no gameplay change.**
  - All the spec-031 / spec-032 measures remain; this is added beside them. Recording and displaying it changes nothing about how a game plays, the characters generated, or any other recorded value.
  - **Acceptance Criteria:**
    - [ ] Given any run that records persona measures, when it is read, then the text-level measures are all still present and the meaning-based one is added beside them.
    - [ ] Given the same games, when they are played with and without this measure recorded, then the game outcomes themselves are identical — only the recorded result gains the new number.

---

## 3. Scope and Boundaries

### In-Scope

- Recording, per measured run, a **meaning-based** (semantic) persona-similarity number, alongside (not replacing) the spec-031 / spec-032 text-level measures.
- Surfacing it in the ledger viewer (run list **and** per-record detail) with its own label, marked as the judgment-based measure, and reading older records that lack it without error.
- **Backfilling** the transcript-preserved past runs, as a purely additive edit to those records.
- Measuring, under **effort-not-results**, whether it captures the archetype-sameness the word-level measures miss (confirmed or refuted).

### Out-of-Scope

- **Replacing or removing** the text-level measures (specs 031 / 032) — they stay; this is additive.
- The **choice of how meaning is judged** (e.g., which AI model or technique assesses it, and whether it returns just a number or a number with an explanation) — that is the technical decision, and because it introduces a model dependency it warrants an architecture decision record.
- **Acting on** the measure (e.g., regenerating colliding characters to force variety) — this only *measures*; changing persona generation in response is separate work.
- Backfilling runs that have **no preserved transcripts** (there is nothing to compute from).
- Any change to **persona generation, game rules, win conditions, or other recorded metrics**.
- All other roadmap items, which are automatically out-of-scope for this specification.
