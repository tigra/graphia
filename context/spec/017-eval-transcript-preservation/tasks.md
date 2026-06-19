# Tasks: Eval Transcript Preservation (Spec 017)

Vertical slices for [spec 017](./functional-spec.md) per its
[technical-considerations](./technical-considerations.md) — Phase 6 / **Eval
Transcript Preservation**. Preserve each measured (eval) game's **full
transcript** — every event in strict chronological order, secrets included (true
roles, private Night picks, personas with a Mafioso's cover *and* true self),
human-readable with `<transcript>`/`<setup>`/`<night>`/`<day>`/`<round>` tags —
and make it **browsable from the eval-ledger viewer** next to the run's metrics.

Each slice leaves the project runnable: **Slice 1** makes eval runs produce
per-game transcript files on disk (readable as plain files, linked from the
ledger record) — the game and normal play are untouched; **Slice 2** surfaces
them in the viewer. Offline verification is `uv run pytest -q` (mock the LLM;
write transcripts into `tmp_path`, never the real `evals/transcripts/`). The
**central design point** held throughout: capture must **tap the per-super-step
stream**, *not* the final snapshot — the per-Night pointing channels are reset
every Night, so a final-state read would lose all but the last Night.
**Eval-only:** the game graph, `driver.py`, normal play, and the
`StreamTraceLogger` are not touched. Agents: `langgraph-agentic` (stream capture
+ renderer), `python-backend` (storage/cleanup/loader/docs), `textual-tui`
(viewer screens), `testing`.

- [x] **Slice 1: Eval runs produce per-game transcripts on disk**
  - [x] **Streaming event capture** — in `src/graphia/tools/blunder_eval.py` (and the `eval_dialogue._drive` it uses) per tech-spec [§2.1](./technical-considerations.md): stop discarding the per-super-step `graph.stream(stream_mode="updates")` updates — add an optional per-update sink (a callback threaded into `_drive`, or a capturing stream loop in the harness) that appends each `{node: delta}` to a new ordered `events` list on `_GameCapture`, recording **in super-step order**: messages added (with their `private_to`), `assign_roles` roles, `generate_personas` personas, `night_round_picks`/`night_rounds_log` (**each Night's picks, before `night_open` resets them**), `active_vote`/ballots, and `kill_log`. This ordered log is the transcript's source of truth; the existing metrics scoring is unchanged. **[Agent: langgraph-agentic]**
  - [x] **Transcript renderer** — a **pure** `render_transcript(events, players, *, game_index, run_meta) -> str` in a new `src/graphia/tools/eval_transcript.py` per tech-spec [§2.2](./technical-considerations.md): render the ordered event log + roles/personas into the tagged, human-readable document — `<transcript>` with a `<setup>` roster (each player's name, **true role**, persona: a Mafioso's public legend **and** its true self; a Citizen's honest persona), then chronological `<night>` (per round: each Mafioso's pick **by name**, then the kill) and `<day>` (per `<round>`: utterances in order, vote initiation, each ballot, the outcome) sections; ids resolved to names; plain prose inside the tags; flat-string output, no I/O. **[Agent: langgraph-agentic]**
  - [x] **Storage + ledger link + cleanup + docs** — per tech-spec [§2.3](./technical-considerations.md): in `blunder_eval.run_eval`, generate a `<run-id>` once (filesystem-safe timestamp) and write each game's rendered transcript to `evals/transcripts/<run-id>/game-NN.txt` (one dir per run, zero-padded game index). Add `run.transcript_dir: "<run-id>"` to `render_record` (defensively absent on older records). Add a **`Makefile` target `make clean-transcripts`** (drop untracked/uncommitted run dirs). **Do NOT gitignore** `evals/transcripts/`. Document the layout + the **commit-full / delete-smoke** cleanup convention + the **assistant-prompt-to-clean-up** norm in `evals/README.md` and `CLAUDE.md`. **[Agent: python-backend]**
  - [x] **Offline tests** — per tech-spec [§4](./technical-considerations.md): new `tests/test_eval_transcript.py` — `render_transcript` over a **synthetic** ordered event log (setup + two multi-round Nights + two Days incl. a vote with ballots + an execution) asserts the `<transcript>`/`<setup>`/`<night>`/`<day>`/`<round>` structure, **strict chronological order**, and that secrets are present (true roles; a Mafioso's `true_self` **and** cover; every Night's picks **by name**; vote ballots + outcome). Extend `tests/test_blunder_eval*.py`: a mocked eval game captures an ordered `events` log including **multiple Nights' picks** (the no-Night-lost regression); a mocked run writes `evals/transcripts/<run-id>/game-NN.txt` into `tmp_path` with the per-run-dir + padded naming, and the record carries the matching `run.transcript_dir`; `make clean-transcripts`'s function drops an untracked run dir (in `tmp_path`). Run `uv run pytest -q`. **[Agent: testing]** *(After this slice: a measured run writes readable, complete transcript files; `make clean-transcripts` exists; the game is unchanged.)*

- [x] **Slice 2: Browse transcripts in the eval-ledger viewer**
  - [x] **Pure transcript loading** — in `src/graphia/eval_ledger.py` per tech-spec [§2.4](./technical-considerations.md): add functions to **locate** a run's transcripts (from a record's `run.transcript_dir` + the ledger's sibling `transcripts/` dir, derived from the ledger `Path` the viewer holds), **list** its `game-*.txt` (sorted), and **read** one — defensive throughout (missing field, missing dir, run not present locally → **empty**, never raises, mirroring `_dig`). No Textual import. **[Agent: python-backend]**
  - [x] **Viewer browse screens** — in `src/graphia/ui/ledger_viewer.py` per tech-spec [§2.4](./technical-considerations.md): from a run's `DetailScreen`, a binding/action opens a `TranscriptListScreen` (the run's games); selecting a game pushes a `TranscriptScreen` — a `VerticalScroll` over the transcript text, **read-only**, with `Esc`/`Backspace`/`q` → `pop_screen`. Reuse spec 012's push/pop screen-stack + cursor/return-on-resume pattern. A run with no transcripts shows a plain **"No transcripts for this run."** The viewer **never writes**. **[Agent: textual-tui]**
  - [x] **Offline tests** — per tech-spec [§4](./technical-considerations.md): pure loading (list/read over a temp `transcripts/` dir; missing dir/field → empty); Pilot — from a run's `DetailScreen` open the transcript list → open a game → the scrollable transcript shows the file's text → back-out returns to the record; a record with **no** transcripts shows the "no transcripts" message; the viewer leaves the files **byte-unchanged** (read-only). Run `uv run pytest -q`. **[Agent: testing]** *(After this slice: `make view-ledger` → drill into a run → read its game transcripts.)*

- [x] **[User/Claude-run, live, optional]** A real `make blunder-eval ARGS="--provider ollama --games 2 --note 'transcript smoke'"`, then `make view-ledger` → drill into the run → **read a game transcript end-to-end** (confirm a real game reads naturally, with the Day/Night structure, roles/personas, and private picks all present and legible) — the one thing the mocked suite can't fully prove. Then clean up per the convention (`make clean-transcripts` for the smoke run, or commit if it holds findings). Optional smoke.

---

_Determinism / secrecy posture (architecture §6): transcripts include secrets (roles, personas, private picks) — fine, they're a maintainer-facing eval artifact never shown to players in-game; normal-play secrecy and non-persistence are untouched. Tests never assert transcript prose verbatim — only structure + contents-present. No new dependency; `safe_llm` untouched (capture reads the stream/state the eval already drives)._

---

## Notes for the implementer

- **Tap the stream, not the snapshot.** The per-Night pointing (`night_round_picks` / `night_rounds_log`) is reset each Night, so the final state holds only the last Night. The capture (Slice 1, task 1) must record per-super-step deltas as they stream — this is the spine; a final-state read silently drops earlier Nights' pointing.
- **Eval-only.** Capture + rendering + storage live in the eval harness (`tools/`); the game graph, `driver.py`, normal play, and the `StreamTraceLogger` are not modified.
- **Not gitignored — curated by convention.** Transcripts are untracked files that hang out until committed or deleted: **commit the full keepers** (e.g. n=20 baselines), **delete the smoke runs** (after a findings check) via `make clean-transcripts`, and the assistant prompts the dev to clean up after a smoke run. An uncommitted run dir left in the tree makes the *next* eval stamp `dirty: true`, so clean-or-commit *before the next measured run*.
- **Tests never write the real `evals/transcripts/`** — always `tmp_path` + an injected dir. No new dependency, no `/awos:hire` — `langgraph-agentic` + `python-backend` + `textual-tui` + `testing` cover everything; verification is offline `uv run pytest -q` (viewer via Pilot).
