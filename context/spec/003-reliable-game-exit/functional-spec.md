# Functional Specification: Reliable Game Exit Controls

- **Roadmap Item:** Polish — refines the "spectator + Ctrl+C" exit handling first introduced by Spec 001 (Playable Skeleton, Slice 9).
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

When a human player is killed or executed mid-game, they enter **spectator mode** — the game keeps running with the AI players, and the human watches. Today, leaving the game from this state isn't reliable: pressing `Esc` collapses the on-screen UI but leaves the program running in the terminal. The only reliable exit is `Ctrl+C`, which is unfamiliar territory for many users.

A spectator should never feel trapped. This spec defines a consistent set of exit controls that work from any point in the game — during the human's own turns, during free-typing day chat, during votes, and during spectator mode. The intent is that pressing `Esc` always offers a clear way out via a quit prompt, while `Ctrl+C` remains the immediate-kill escape hatch for power users. The letter `q` stays unbound — players must be able to type words starting with "q" (queen, quiet, question…) in day chat without triggering anything.

**Success looks like:** a human player who has just been killed (or who simply wants to leave a game in progress) presses `Esc`, sees a "Quit? (y/n)" prompt, picks `y`, and lands back in their shell — every time, regardless of what was on screen a moment earlier.

---

## 2. Functional Requirements (The "What")

### 2.1 Quit confirmation prompt (`Esc`)

- **As a** human player at any point during a Graphia game, **I want to** press `Esc` and be asked to confirm before the game ends, **so that** I can leave without losing the game to an accidental keypress.
  - **Acceptance Criteria:**
    - [x] Pressing `Esc` from any in-game screen (role-count prompt, Night phase, Day chat, an open vote, spectator view, end-of-game screen) opens a small confirmation overlay with the text "Quit? (y/n)" (or equivalent clearly-labelled prompt).
    - [x] While the confirmation overlay is visible, pressing `y` (or `Enter` if the affirmative option is focused) closes the game and returns the user to their shell with no stack trace.
    - [x] While the confirmation overlay is visible, pressing `n` dismisses the overlay and returns the user to the exact game state they were in — nothing lost, no message in the public log.
    - [x] Pressing `Esc` while the overlay is visible has the same effect as `n` — dismisses the overlay.

### 2.1a `q` is NOT a quit key

- **As a** human player typing freely in day chat, **I want to** be able to type words like "queen", "quiet", or just the letter "q", **so that** the chat doesn't unpredictably switch into a quit confirmation.
  - **Acceptance Criteria:**
    - [x] Pressing `q` at any in-game screen — including while a text input has focus and while the input is empty — does NOT open the quit confirmation overlay.
    - [x] When a text input has focus, pressing `q` types a `q` character into the input as normal.
    - [x] When no text input has focus, pressing `q` is silently ignored (no action, no message).
    - [x] Removing the previous behaviour (q opening the quit prompt) does not affect Esc or Ctrl+C in any way.

### 2.2 Spectator-mode exit (the headline case)

- **As a** human player who has just been killed or executed, **I want to** leave the game cleanly with a single key, **so that** I am not forced to watch the rest of a game I no longer care about.
  - **Acceptance Criteria:**
    - [x] Pressing `Esc` after the "You have been killed. Watching as a spectator." message is displayed opens the quit confirmation overlay defined in §2.1. Picking `y` returns the user to the shell.
    - [x] AI players continue their game in the background while the confirmation overlay is visible. The game state when the overlay is dismissed (`n` / `Esc`) is the live, current state — not the moment the overlay appeared.

### 2.3 Immediate-kill (`Ctrl+C`)

- **As a** human player who is sure they want out, **I want** `Ctrl+C` to exit the game immediately without any prompt, **so that** I retain the conventional "force-quit" terminal affordance.
  - **Acceptance Criteria:**
    - [x] Pressing `Ctrl+C` at any point during the game closes the program immediately, with no confirmation, regardless of whether a quit confirmation overlay is currently visible.
    - [x] The exit is clean — no stack trace, no orphan process.

### 2.4 Bug fix — `Esc` no longer half-exits

- **As a** human player, **I want** `Esc` to do something predictable, **so that** I don't end up with a closed-looking UI but a still-running program.
  - **Acceptance Criteria:**
    - [x] Pressing `Esc` never collapses, hides, or unmounts any portion of the game UI without also either opening the quit confirmation or closing the program entirely. The previous half-exit behaviour is gone.

---

## 3. Scope and Boundaries

### In-Scope

- The quit confirmation overlay (text, key bindings, behaviour on `y` / `n` / `Esc`).
- `Esc` as the trigger for the confirmation, working from every game screen including spectator mode and end-of-game.
- `Ctrl+C` preserved as the immediate-kill key with no confirmation.
- The fix to the existing "Esc collapses the UI but leaves the process running" behaviour.
- AI players continue running in the background while the overlay is visible; dismissing returns to the live state.
- `q` is NOT bound to any action — it is a normal printable character in text inputs and a no-op elsewhere.

### Out-of-Scope

- A "main menu" or any in-app navigation away from a running game (the only exits are "back to shell").
- A "save and quit" / "resume later" feature.
- Mouse-based UI affordances (clicking a Close button) — the project is terminal-first and keyboard-only.
- All remaining roadmap items: Long-Term Cross-Game Memory & Career Stats, AI Provider Flexibility, Setup Flexibility, Richer Night Resolution, AI Personas & Per-Game Memory, Asynchronous Day Chat, End-of-Game Payoff, AI Tool-Use Demonstration, Expanded Role Roster.