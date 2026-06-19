# Technical Specification: Eval Transcript Preservation

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

The eval harness (`src/graphia/tools/blunder_eval.py`) already plays N games per run and appends a metrics record to the committed ledger. This increment adds, per game, a **captured chronological event log → a rendered human-readable transcript → a file under a per-run directory**, plus a **browse path in the eval-ledger viewer**.

The load-bearing decision is **where the events come from**. The harness today drives each game through `eval_dialogue._drive`, which iterates `graph.stream(stream_mode="updates")` and **discards** the per-super-step updates, then reads the *final* state via `graph.get_state`. That snapshot is insufficient for a faithful transcript: the per-Night pointing channels (`night_round_picks` / `night_rounds_log`) are **reset every Night** (in `night_open`), so the final state holds only the *last* Night's picks. To satisfy "all events in strict chronological order" (functional-spec §2.1), transcript capture must **record each super-step's update as it streams** — the same event shape the `StreamTraceLogger` consumes for the interactive game, but accumulated in-memory by the eval and rendered to a readable transcript.

So: tap the stream into an ordered per-game event log → a **pure renderer** turns that log (plus the final roles/personas) into a tagged, human-readable transcript (`<transcript>` / `<setup>` / `<night>` / `<day>` / `<round>`, secrets included) → written to `evals/transcripts/<run-id>/game-NN.txt` → the ledger record carries the `<run-id>` so the **viewer** (`eval_ledger.py` + `ui/ledger_viewer.py`) can locate and browse them. `evals/transcripts/` is **gitignored** so routine runs don't re-dirty the working tree (the §4 consideration); a developer shares a run with `git add -f`. **Eval-only** — the game graph, driver, normal play, and the `StreamTraceLogger` are untouched.

Stacks: **`langgraph-agentic`** (the stream-event capture + the transcript renderer — both lean on graph-stream/state semantics), **`textual-tui`** (the viewer's transcript-browse screens), **`testing`**. No new dependency, no new technology → **no `/awos:hire`**.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Per-game streaming event capture — `src/graphia/tools/blunder_eval.py` (+ the drive helper it uses) — **[Agent: langgraph-agentic]**

- The per-game drive must stop discarding stream updates. Add an **optional per-update sink** to the drive path (either thread an `on_update` callback into `eval_dialogue._drive`, or have `blunder_eval` run its own capturing `graph.stream(stream_mode="updates")` loop for eval games) that appends each super-step's `{node: delta}` to a new ordered `events` list on `_GameCapture`.
- The captured log records, **in super-step order**, the events a transcript needs: messages added that step (with their `additional_kwargs["private_to"]`), and the relevant state deltas — `assign_roles` (roles), `generate_personas` (personas), `night_round_picks` / `night_rounds_log` (each Night's pointing, **before they're reset**), `active_vote` / ballots (vote initiation, each ballot, outcome), and `kill_log` (kills). This ordered log — **not** the final snapshot — is the transcript's source of truth.
- The existing metrics scoring (repetition, the blunder family) is **unchanged** — it keeps reading the final messages / capture-proxy outputs; this only *adds* an event log alongside.
- **Eval-only:** the capture lives entirely in the harness. The game graph, `driver.py`, and `StreamTraceLogger` are not modified.

### 2.2 Transcript renderer — a pure module (e.g. `src/graphia/tools/eval_transcript.py`) — **[Agent: langgraph-agentic]**

- A pure `render_transcript(events, players, *, game_index, run_meta) -> str` (no I/O, unit-testable) that turns the ordered event log + final roles/personas into the document. Structure (the tags from functional-spec §2.1 — readability markers, not a format requiring a parser):
  - `<transcript>` wrapper with a `<setup>` block — the roster: each player's name, **true role**, and persona (a Mafioso's **public legend *and* its true self**; a Citizen's honest persona).
  - alternating `<night>` (per Night: each round's Mafioso picks **by name**, then the kill) and `<day>` (per Day: a `<round>` per speaking round — utterances in order, vote initiation, each ballot, the outcome) sections, in **strict chronological order** as they streamed.
  - Plain readable prose inside each tag; ids resolved to names throughout.
- Flat-string output (same contract as `eval_ledger.render_detail` / `blunder_eval.render_record`). Written as a `.txt` file so it opens as a plain readable file (functional-spec §2.3) and the future judge can parse it by its tags.

### 2.3 Per-run storage layout, sharing, and cleanup convention — `src/graphia/tools/blunder_eval.py`, `Makefile`, `evals/README.md`, `CLAUDE.md` — **[Agent: python-backend]**

- **Layout:** each game's transcript is written to `evals/transcripts/<run-id>/game-NN.txt` — one **directory per run** (functional-spec §2.3), zero-padded game index for clear naming. `<run-id>` is generated once per `run_eval` (a filesystem-safe timestamp, e.g. `2026-06-18T14-32-05`, optionally with a short suffix to avoid collisions).
- **Ledger link:** `render_record` gains a reference to the run's transcript dir — `run.transcript_dir: "<run-id>"` (a new field; defensively absent on older records). This is how the viewer maps a record → its transcripts.
- **Not gitignored — visible and curated.** Transcripts are written into the tracked tree as ordinary (untracked) files and simply *hang out* until the developer **commits** them or **deletes** them — the developer, not an ignore rule, decides which runs join the shared record. This is a deliberate choice (visibility + curation) over a silent ignore.
- **Cleanup convention (documented, not code-enforced):** commit the full, clean runs worth keeping (e.g. the n=20 baselines); delete the few-game **smoke** runs *before committing*, after confirming they hold no important findings. Provide a one-command cleanup — a `Makefile` target (e.g. `make clean-transcripts`, dropping untracked/uncommitted run dirs, or a single named run) — so cleanup isn't manual `rm`.
- **Assistant norm (CLAUDE.md):** document that after a **smoke** (few-game) eval run the assistant should **prompt the developer to delete that run's transcripts** (unless they hold findings), and commit full runs. This is also what keeps eval provenance clean: an uncommitted transcript dir left in the tree makes the *next* run stamp `dirty: true`, so commit-or-delete *before the next measured run* is the discipline.
- Document the layout + convention in `evals/README.md` next to the ledger contract.

### 2.4 Browse transcripts in the viewer — `src/graphia/eval_ledger.py` (pure) + `src/graphia/ui/ledger_viewer.py` (UI) — **[Agent: textual-tui]**

- **Pure layer (`eval_ledger.py`):** add transcript-locating/loading given a record's `run.transcript_dir` and the ledger's sibling `transcripts/` dir (derived from the ledger `Path` the viewer already holds): list a run's `game-*.txt` files (sorted), and read one. Defensive — a missing `transcript_dir` field, or a dir that isn't present locally (run not shared/pulled), resolves to **empty** (drives the "no transcripts" state), never raises (mirrors `_dig`).
- **UI (`ledger_viewer.py`):** from a run's `DetailScreen`, a binding/action opens a **`TranscriptListScreen`** (the run's games); selecting a game pushes a **`TranscriptScreen`** — a `VerticalScroll` over the transcript text, read-only, with the same `Esc`/`Backspace`/`q` → `pop_screen` back-out as `DetailScreen`. Reuse spec 012's push/pop screen-stack + cursor/return-on-resume pattern. When a run has no transcripts, the list screen shows a plain "No transcripts for this run." message (functional-spec §2.2). The viewer **never writes** — read-only throughout.

---

## 3. Impact and Risk Analysis

- **Blast radius:** `blunder_eval.py` (capture + write + the record's `transcript_dir`), a new `eval_transcript.py` renderer, `eval_ledger.py` + `ui/ledger_viewer.py` (browse), `.gitignore`, `evals/README.md`, tests. **No change** to the game graph, `driver.py`, normal play, the `StreamTraceLogger`, or the metrics scoring.
- **Risk — the final-state snapshot loses per-Night data** (the central one). *Mitigation:* §2.1 captures the streaming per-super-step deltas as the source of truth, so every Night's pointing is recorded before `night_open` resets it.
- **Risk — repo bloat / a perpetually-dirty tree.** Transcripts are deliberately **not** gitignored, so a run leaves untracked files in `git status`, and — left in place — they'd make the *next* eval run stamp `dirty: true`, and committing every run would bloat the repo. *Mitigation:* the **commit-or-delete convention** (§2.3) applied *before the next measured run* — commit the full keepers, delete the smoke runs (one-command `make clean-transcripts`), with the assistant prompting for cleanup. This is a **workflow discipline, not a code guarantee**; the user chose visibility + curation over a silent ignore, accepting the transient-dirty window.
- **Risk — viewer must tolerate records with no transcripts** (older runs; runs whose dir wasn't shared or pulled). *Mitigation:* defensive locate → "no transcripts" state (§2.4), exactly like the viewer's existing pre-provenance handling.
- **Risk — capture perturbs the eval.** *Mitigation:* the capture is **passive** (it records deltas the stream already emits); it doesn't change the graph run, the interrupts, or the metrics. It only stops the `for _ in stream: pass` from throwing the updates away.
- **Secrecy / determinism posture:** the transcript deliberately includes secrets (roles, personas, private picks) — acceptable because it is an **eval artifact for the maintainer**, never shown to players in-game; normal-play secrecy and non-persistence are untouched. Per architecture §6 the games are non-deterministic, so transcripts are never asserted verbatim in tests — only their structure/contents-present. (Product-def §3.2 lists full-transcript persistence as out-of-scope for *gameplay*; this is the eval-only exception, like the committed ledger — a product-def footnote may be warranted later, flagged in the functional spec.)

---

## 4. Testing Strategy — **[Agent: testing]**

Offline and model-free throughout; write transcripts into `tmp_path`, never the real `evals/transcripts/`.

- **Pure renderer (`tests/test_eval_transcript.py`):** `render_transcript` over a **synthetic ordered event log** (hand-built: setup with roles/personas, two Nights of multi-round pointing, two Days of utterances + a vote with ballots + an execution) → assert the `<transcript>`/`<setup>`/`<night>`/`<day>`/`<round>` structure, **strict chronological order**, and that the secrets are present (true roles; a Mafioso's `true_self` *and* its cover legend; every Night's picks **by name**; vote initiation + each ballot + outcome). Never assert prose verbatim beyond the synthetic inputs.
- **Capture (extend `tests/test_blunder_eval*.py`):** drive a mocked eval game (fake LLM + monkeypatched RNG) and assert the per-game `events` log captures the ordered events including **multiple Nights' picks** — the regression that proves earlier Nights aren't lost to the reset (the failure mode a final-state read would have).
- **Storage:** a mocked eval run writes `evals/transcripts/<run-id>/game-NN.txt` (into `tmp_path`) with the per-run-dir + zero-padded naming, and the ledger record carries the matching `run.transcript_dir`.
- **Viewer (`tests/test_ledger_model.py` + `tests/test_ledger_viewer.py`):** pure transcript-locating/loading over a temp `transcripts/` dir (list + read; missing dir/field → empty); Pilot — from a run's `DetailScreen`, open the transcript list → open a game → the scrollable transcript shows the file's text → back-out returns to the record; a record with **no** transcripts shows the "no transcripts" message; the viewer leaves files byte-unchanged (read-only).
- **Cleanup affordance:** test that the `make clean-transcripts` target / its underlying function removes a run's transcript dir (operating in `tmp_path`, never the real `evals/transcripts/`), and that it drops only untracked/uncommitted run dirs (doesn't touch committed ones).
- No new dependency; `safe_llm` untouched (the capture reads the stream/state the eval already drives).
