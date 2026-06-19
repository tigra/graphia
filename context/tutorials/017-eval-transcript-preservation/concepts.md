---
spec: 017-eval-transcript-preservation
spec_title: Eval Transcript Preservation
introduced_on: 2026-06-19
---

# Concepts introduced in this increment

Each entry carries a human-readable **title** (what the tutorial shows the reader) and a stable kebab-case **slug** (the internal dedup key future tutorials look up). Grouped by the domain the spec exercises.

## Capture

- **Capture the event stream, not the final snapshot** (`stream-tap-capture-not-snapshot`) — The transcript's source of truth is the ordered per-super-step `{node: delta}` log accumulated *as the game streams*, because the per-Night pointing channels are replace-reduced and reset each Night, so a final `get_state` snapshot would keep only the last Night.
- **Optional per-super-step sink on the shared driver** (`optional-on-update-sink`) — `_drive` gains a keyword-only `on_update` callback (default `None`, so every existing caller is byte-for-byte unchanged); the eval passes a sink that appends each streamed update to the game's event log — a passive tap, not a fork.

## Rendering

- **Reconstructing phase structure from a flat delta stream** (`phase-reconstruction-from-delta-stream`) — A pure renderer walks the flat `{node: delta}` log and rebuilds nested `<night>`/`<day>`/`<round>` structure using the engine's own node-name deltas as boundaries (`night_open`, `day_open`, a *failed* `resolve_vote`), accumulating each Night's picks before the next `night_open` resets the channels.
- **Rendering messages by voice** (`render-messages-by-voice`) — Each `messages` delta is dispatched by message type into a distinct voice: an `AIMessage` becomes `<name>: <text>`, a public `SystemMessage` becomes `Moderator: …`, and a private `SystemMessage` (carrying `additional_kwargs["private_to"]`) becomes `Moderator (private to <name>): …` — kept, not stripped.
- **A secrets-included maintainer artifact** (`secrets-included-eval-artifact`) — The transcript deliberately inverts the game's in-play secrecy (true roles, private Night picks, both persona layers, the private whispers all shown) because it is an after-the-fact artifact for the maintainer and the future LLM-as-Judge, never seen by a player mid-game.

## Storage & sharing

- **Per-run transcript files with injectable roots** (`per-run-transcript-storage`) — Each run's games are written to `evals/transcripts/<run-id>/game-NN.txt` (one directory per run, zero-padded index, a filesystem-safe timestamp run-id), with the transcripts root and run-id injectable so tests write into `tmp_path` and never the real store.
- **Linking a ledger record to its transcripts** (`record-transcript-dir-link`) — `run_eval` stamps `run.transcript_dir = <run-id>` on the record (only when ≥1 transcript was written, defensively absent otherwise); the viewer derives the absolute path from the ledger's sibling `transcripts/` directory, so a record points at its games without storing an absolute path.
- **Visible-and-curated, not gitignored** (`curated-by-convention-not-gitignore`) — Transcripts are written into the tracked tree as ordinary *untracked* files that remain until the developer commits or deletes them; `make clean-transcripts` drops only the untracked (smoke) run dirs, the convention is commit-full / delete-smoke, and the trade-off is visibility-plus-curation over a silent ignore (the cost: an uncommitted run dir makes the next eval stamp `dirty: true`).

## UI

- **Browsing transcripts in the eval viewer** (`transcript-browse-screens`) — A Textual-free locator/loader (`transcript_dir_for` / `list_transcripts` / `read_transcript`) drives two new screens — a `TranscriptListScreen` opened by a `t` binding on the run's `DetailScreen`, and a read-only `TranscriptScreen` — reusing spec 012's push/pop drill-down and showing a plain "No transcripts for this run." for older records.

## Testing

- **The early-bound default that leaked into the real ledger** (`early-bound-default-leak`) — A `ledger_path: Path = LEDGER_PATH` signature default is bound once at import, so a test's `monkeypatch.setattr(LEDGER_PATH)` never reaches a no-arg call inside `run_eval` and synthetic records leak into the committed ledger; the fix is to default the parameter to `None` and resolve the module global *at call time*.
