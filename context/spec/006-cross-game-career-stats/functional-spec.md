# Functional Specification: Long-Term Cross-Game Memory & Career Stats

- **Roadmap Item:** Phase 3 — Long-Term Cross-Game Memory & Career Stats (Cross-Game Stats Accumulation, Pre-Game Career-Summary Greeting, Post-Game Career-Stats Panel, AgentCore Memory as the Long-Term Store, Local-File Stats Store)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today every game of Graphia is an island: when a game ends, nothing about it is remembered. The next launch starts cold, with no sense that the player has a history. This makes the game feel disposable and — for the project's reference purpose — leaves the long-term, cross-session memory pattern undemonstrated.

This change gives the player a **persistent career**. Across every game they play, Graphia quietly keeps a running tally of how they've done and what happened in their games. On each launch the player is greeted with a short summary of that history; at the end of each game they see how the just-finished game moved their numbers.

**Desired outcome:** a returning player feels recognized ("you've played 8 games, you're better as a Citizen than as Mafia") and can watch their numbers grow over time. **Success is measured** by: after at least one finished game, both the launch greeting and the end-of-game panel show accurate cumulative numbers that include the game just played, and those numbers are the same whether the player runs the game on their own machine or against the hosted version.

---

## 2. Functional Requirements (The "What")

### 2.1 Your career is remembered across games

- **As a** returning player, **I want** my results and actions to carry over from one game to the next, **so that** I build up a real history instead of starting fresh every time.
  - **Acceptance Criteria:**
    - [ ] When a game finishes by a side winning, the player's tallies are updated to include that game.
    - [ ] When a game is abandoned by the player confirming a quit, the player's tallies are updated to record it as an abandoned game (see §2.7).
    - [ ] Once a game has been recorded, its contribution to the history is still present on the next launch.
    - [ ] Closing and reopening the game does not reset or lose previously accumulated history.
    - [ ] Only summary numbers are remembered — the player cannot replay or re-read the transcript, chat, or diaries of a past game.

### 2.2 Pre-game career greeting

- **As a** player, **I want** a short summary of my history when I launch the game, **so that** I'm reminded where I left off before the new game begins.
  - **Acceptance Criteria:**
    - [ ] On launch, before the game begins, the player sees a one-paragraph summary of their cumulative career.
    - [ ] The greeting reports, at minimum: total games played, win rate broken down by role (as Mafia / as Law-abiding), kills attempted vs. successful, and votes the player has initiated.
    - [ ] **First run:** when there is no history yet, the greeting is replaced by a short one-line welcome (e.g., *"Welcome — this is your first game, so there's no history yet."*), after which the game proceeds normally.
    - [ ] The greeting is informational only — it requires no input and does not interrupt the start of the game.

### 2.3 Post-game career panel with deltas

- **As a** player, **I want** to see how the game I just finished changed my numbers, **so that** each game visibly contributes to my career.
  - **Acceptance Criteria:**
    - [ ] After the Moderator's end-of-game recap (i.e., when a game ends by a side winning), the player sees a brief career-stats panel.
    - [ ] The panel shows the player's updated cumulative numbers **and** the change ("delta") contributed by the game just finished (e.g., *"You initiated 1 day-vote today — career total: 6"*).
    - [ ] Every personal counter that changed during the game shows its delta; counters that did not change may show no delta or a zero delta.
    - [ ] The numbers in the panel reflect the game that just ended (they are not "one game behind").
    - [ ] When a game ends by the player quitting (no recap is shown), no post-game panel appears; the abandoned game is instead reflected in the next launch's greeting.

### 2.4 What's counted — your personal numbers

- The game tracks, for the human player, across all games:
  - **Games played**, split by the role the player held (as Mafia / as Law-abiding).
  - **Wins and win rate, by role.** A game counts as a win for the player when the player's own side won that game.
  - **Day-votes you called** — the number of times the player opened a vote to execute someone.
  - **Day-ballots you cast** — the number of execution votes the player took part in.
  - **Night kills attempted vs. successful (as Mafia)** — *attempted* counts each Night the player took part in choosing a victim; *successful* counts the Nights where the victim the Mafia killed was the one the player backed.
  - **Acceptance Criteria:**
    - [ ] After a game in which the player (as Mafia) backed the target that was actually killed, the "successful kills" number increases by one.
    - [ ] After a game in which the player (as Mafia) backed a target that was *not* the one killed, "attempted" increases but "successful" does not.
    - [ ] After a game the player won, the win count and win rate for the role they played increase accordingly.
    - [ ] Win rate is computed only over games that reached a win/loss; abandoned games are excluded from the win-rate denominator.
    - [ ] When the player has not yet completed a game in a given role, the win rate for that role reads as not-applicable (e.g., *"—"* or *"no games yet"*) rather than as 0%.
    - [ ] Counters reflect actions the player took while alive; being eliminated early does not erase the actions taken before elimination.

### 2.5 What's counted — game-wide totals

- Alongside the player's personal numbers, the greeting and panel show totals spanning **all** games played:
  - **Games & outcome split** — total games played and how they ended: Law-abiding wins, Mafia wins, and abandoned games (three categories).
  - **Total day executions** — cumulative count of players voted out during Day phases.
  - **Total night victims** — cumulative count of players killed at Night.
  - **Average game length** — the average number of day/night rounds a game lasts, computed over games that played to a win/loss (abandoned games, which have no final length, are excluded from this average).
  - **Acceptance Criteria:**
    - [ ] After each finished game, the "games played" total and the matching outcome tally (Law-abiding win, Mafia win, or abandoned) each increase by one.
    - [ ] The "total day executions" and "total night victims" totals increase by the number of players executed and killed in the just-recorded game, including events that occurred before a quit.
    - [ ] The "average game length" reflects all games that played to a win/loss, including the most recent such game, and is unaffected by abandoned games.
    - [ ] Game-wide totals are clearly distinguishable from the player's personal numbers (the player can tell which figures are "you" and which are "all games").

### 2.6 Same experience however the game is run

- **As a** player, **I want** my career to behave identically whether I play on my own machine or against the hosted version, **so that** my history is one continuous record from my point of view.
  - **Acceptance Criteria:**
    - [ ] The greeting and the post-game panel look and read the same regardless of how the game is launched.
    - [ ] The set of numbers tracked is the same in both cases.
    - [ ] *(Note: where the history is physically kept differs between the two run modes; that is an implementation detail and is addressed in the technical considerations, not here.)*

### 2.7 Games that end by quitting (abandoned games)

- **As a** player who leaves a game in progress, **I want** that game to be acknowledged in my history rather than vanish, **so that** my record reflects every game I started.
  - **Acceptance Criteria:**
    - [ ] When the player confirms a quit (the "Quit? (y/n)" prompt → yes) during a game in progress, that game is recorded as an **abandoned** game before the program exits.
    - [ ] An abandoned game adds one to "games played" for the role the player was holding and adds one to the "abandoned" category in the game-wide outcome split.
    - [ ] An abandoned game does **not** count as a win or a loss and is excluded from win-rate calculations.
    - [ ] The player's personal action counters (day-votes called, day-ballots cast, night kills attempted/successful) include the actions the player actually completed before quitting.
    - [ ] Pressing `Ctrl+C` to force-quit exits the program immediately and records **nothing** — no abandoned game is added. This is an accepted, deliberate difference from the `Esc`-confirmed quit path. *(Players who want the game recorded should leave via `Esc` → yes; `Ctrl+C` remains the instant escape hatch.)*

---

## 3. Scope and Boundaries

### In-Scope

- Persisting a small per-game summary after every recorded game (a win/loss, or an `Esc`-confirmed quit) and accumulating it into a running career history.
- A one-paragraph pre-game greeting on launch (with a first-run welcome variant).
- A post-game career-stats panel, shown after the Moderator recap on a win/loss, with cumulative numbers and per-game deltas.
- The personal counters in §2.4 and the game-wide totals in §2.5, including the three-way outcome split (Law-abiding win / Mafia win / abandoned).
- Recording an abandoned game on an `Esc`-confirmed quit (§2.7).
- Identical player-facing behavior across both run modes.

### Out-of-Scope

- Recording anything on a `Ctrl+C` force-quit — that path exits instantly and is intentionally not captured.
- A standalone "show me my stats" command — career numbers appear only via the launch greeting and the post-game panel within a normal game.
- Persisting full game transcripts, day-chat, diaries, or vote-by-vote replays across sessions — only the summary numbers are kept.
- Save/resume of an in-progress game — each game is still a fresh session; "abandoned" is an outcome, not a resumable save.
- Any change to how a single game plays out (Night/Day mechanics, win conditions, voting rules) or to the existing quit controls themselves (Esc prompt, Ctrl+C) beyond adding the abandoned-game record.
- **Other roadmap items, added to Out-of-Scope as separate specs:** Phase 4 — AI Provider Flexibility (AWS Profile/SSO, Local Ollama); Phase 5 — Setup Flexibility (Configurable Role Counts) and Richer Night Resolution (Multi-Round Mafia Consensus); Phase 6 — AI Personas & Per-Game Memory (Character Sheets, Per-AI Private Diaries); Phase 7 — AI Tool-Use Demonstration and Expanded Role Roster.
