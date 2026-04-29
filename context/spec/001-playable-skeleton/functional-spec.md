# Functional Specification: Playable Skeleton

- **Roadmap Items:** Phase 1 — Preset Game Lifecycle (Fixed Starter Lineup, Moderator & Phase Alternation, Win Condition Detection & Endgame) + Core Gameplay Loop (Single-Round Night Kill by Majority Vote, First-Night Mafia Introductions, Day Discussion & Vote-to-Execute, Human-in-the-Loop Turns)
- **Status:** Completed
- **Author:** Poe (on behalf of the project owner)

---

## 1. Overview and Rationale (The "Why")

Graphia's first spec delivers the smallest **complete, playable end-to-end Mafia game** — a version a player can actually sit down with, play to a decisive ending, and see both sides' win conditions work. This spec deliberately omits everything that adds flavor or modern polish (configurable role counts, multi-round consensus, AI personalities, private diaries, asynchronous chat, the creative end-of-game recap) — those arrive in later specs that layer onto this skeleton.

The purpose is to prove that the core game loop — role assignment, Night kills, Day discussion and execution votes, win detection — works correctly in isolation, before any of the richer features are wired in. Once this skeleton runs without glitches, later work becomes additive rather than coupled to fixing foundation bugs.

**Success looks like:** A first-time player can run the game from the command line and play an entire session (either as a Mafioso or a Law-abiding Citizen), reach a decisive ending for one side or the other, and never be confused about whose turn it is, what phase they're in, or whether the game has ended.

---

## 2. Functional Requirements (The "What")

### 2.1 Starting a new game

- **As a player**, **I want to** launch the game with a single command and give it my name, **so that I can** start playing immediately with a personalized identity.
  - **Acceptance Criteria:**
    - [x] Launching the game opens a console interface with a welcome screen from the Moderator.
    - [x] The welcome screen prompts the player for their display name.
    - [x] After the player enters a name, a brief "Gathering players…" message is shown while the roster is assembled, so the screen is never frozen.
    - [x] The game starts with a fixed lineup of 7 players total: 2 Mafia and 5 Law-abiding Citizens. The player is one of the 7; their role is assigned randomly.
    - [x] The 6 non-human players are given fresh, distinct names generated at the start of each run, so two consecutive runs show different rosters.

### 2.2 Roster introduction and role reveal

- **As a player**, **I want to** see who everyone else in the game is and learn my own role, **so that I can** play with the right information from the first Night.
  - **Acceptance Criteria:**
    - [x] After the roster is assembled, the Moderator publicly introduces all 7 players by name in a single roster announcement — names only, no roles.
    - [x] Immediately after the public introduction, the Moderator sends the player a private message of the form: "You are [player's chosen name]. Your role is [Mafia | Law-abiding Citizen]."
    - [x] The private role message is visually distinct from the public Moderator announcements so the player understands it is only for them.
    - [x] No other roles are revealed at this point — the player only knows their own.

### 2.3 First-Night Mafia introductions

- **As a Mafia player**, **I want to** learn who my teammates are on the first Night, **so that I can** coordinate with them (and, as the human, know whom I'm rooting for).
  - **Acceptance Criteria:**
    - [x] If the human player drew the Mafia role, at the start of Night 1 the Moderator sends them a private message listing the names of the other Mafia player(s): "Your Mafia teammates are: [Name]." (In this 2-Mafia preset there is exactly 1 other Mafia.)
    - [x] If the human player drew the Law-abiding role, they see only the standard "Night falls" announcement and no private Mafia message.
    - [x] Mafia introductions happen only on Night 1, not on subsequent nights.
    - [x] The AI Mafia players are also told each other's identities at this point, privately and invisibly to the human (since the human would not see that in a real game).

### 2.4 Night phase — pointing and kill resolution

- **As a Mafia player**, **I want to** point at the Law-abiding Citizen I want to kill and see a result, **so that** the Mafia make progress toward victory each Night.
  - **Acceptance Criteria:**
    - [x] At the start of each Night (after any first-Night introductions), the Moderator announces "Night falls." to everyone.
    - [x] Each alive Mafia player (human and AI) is privately shown the current list of alive Law-abiding Citizens and asked to pick one as their target.
    - [x] Mafia players do not see each other's picks while choosing — each Mafia's selection is private to them until the tally.
    - [x] The human Mafia player picks their target by selecting a name from the displayed list of alive Law-abiding Citizens; they cannot pick themselves, other Mafia, or dead players.
    - [x] Law-abiding players see no pointing interface during the Night — only the "Night falls." announcement, and then they wait.
    - [x] Once every alive Mafia has picked, the Moderator tallies the picks and selects the victim by strict majority. If there is a tie, one of the tied targets is chosen at random.
    - [x] The Moderator then publicly announces the victim by name: "During the night, [Name] was killed." The victim's role is **not** revealed at this point — it will be disclosed the following morning, when Day breaks.
    - [x] If every Mafia happens to point at the same target, that target is the victim with no tie-break needed.

### 2.5 Day phase — round-robin discussion

- **As a player**, **I want to** take structured turns speaking during the Day, **so that I** know when it's my turn and the conversation doesn't devolve into chaos.
  - **Acceptance Criteria:**
    - [x] Each Day begins with the Moderator announcing "Day breaks.", recalling who was killed the previous Night, and revealing that victim's true role ("[Name] was a [Mafia | Law-abiding Citizen]."). On the very first Day, when no one has been killed yet, this recap step is skipped.
    - [x] A Day consists of speaking rounds. In each round, every alive player is given one turn, in a random order chosen fresh for that round.
    - [x] On their turn, a player does exactly one of two things: (a) speak a short line to the whole group, or (b) call a vote to execute a specific named player. A turn cannot do both.
    - [x] When it is the human player's turn, the interface clearly prompts them ("It's your turn. Speak a short line, or type `/vote <name>` to call a vote."), accepts a short text message as their spoken line, and also accepts the vote command.
    - [x] When it is an AI player's turn, the Moderator displays that player's short spoken line (or vote announcement) to everyone.
    - [x] If a full speaking round completes without any vote being called, a new round begins with a freshly shuffled order.
    - [x] A Day ends when either (a) a vote succeeds in executing someone, (b) three votes have been called that Day without any succeeding, or (c) six full speaking rounds have passed without anyone calling a vote (safety cap to prevent endless talking).
    - [x] When a Day ends without an execution, the Moderator announces "The Day ends with no one executed." and Night begins.

### 2.6 Day phase — vote-to-execute

- **As a player**, **I want to** be able to call a vote to execute someone I suspect, and I want to cast my own vote when a vote is called, **so that** the Law-abiding side has a way to fight back and the Mafia have a way to eliminate accusers.
  - **Acceptance Criteria:**
    - [x] A vote is initiated on a player's turn when they call `/vote <name>` (human) or when an AI chooses to initiate a vote instead of speaking. The name must be an alive player (including the initiator themselves, in theory).
    - [x] When a vote is initiated, the Moderator pauses the speaking round and announces: "[Initiator] has called for a vote to execute [Target]."
    - [x] Every alive player, including the initiator and the target, is polled in roster order for a Yes/No vote.
    - [x] The human player, when polled, is prompted: "Execute [Target]? (y/n)".
    - [x] Each AI player casts Yes or No; their vote is announced out loud by the Moderator as they cast it so everyone can see the running tally.
    - [x] After all votes are cast, the Moderator announces the tally ("3 Yes, 2 No") and the outcome.
    - [x] The target is executed only with a strict majority of Yes votes (more than half of the alive players). Ties do not execute.
    - [x] If the target is executed, the Moderator announces "[Target] has been executed." and **immediately reveals the target's true role** ("[Target] was a [Mafia | Law-abiding Citizen]."). The Day ends right after the reveal.
    - [x] If the vote fails, the Moderator announces "The vote fails." and a new speaking round begins with a fresh random order. The failed vote counts against the three-vote-per-Day cap.

### 2.7 Win condition detection and ending the game

- **As a player**, **I want to** see a clear, decisive ending when one side wins, **so that I** know the game is over and understand the outcome.
  - **Acceptance Criteria:**
    - [x] After every Night resolution and every Day resolution, the game checks whether one side has won.
    - [x] The Law-abiding side wins when every Mafia player has been eliminated.
    - [x] The Mafia side wins when the number of surviving Mafia players is greater than or equal to the number of surviving Law-abiding players.
    - [x] When either side wins, no further phases are played.
    - [x] The Moderator shows an end-of-game screen containing: (a) a one-line winner announcement ("The Law-abiding Citizens have won." / "The Mafia have won."), (b) a chronological list of who was killed at Night and who was executed during the Day, with the corresponding Day number, and (c) a full roster reveal showing every player's true role (Mafia or Law-abiding).
    - [x] After the end-of-game screen, the game waits for a keypress, then exits cleanly back to the shell.

### 2.8 Spectator view after the player dies

- **As a player** whose character has been killed or executed, **I want to** keep watching the game play out until it ends, **so that I can** see what happens without being frozen or forced to quit.
  - **Acceptance Criteria:**
    - [x] When the player's character is killed at Night or executed during the Day, the Moderator announces the death as normal and the player's screen enters spectator mode.
    - [x] From that point, the player continues to see all Moderator announcements, AI dialogue, vote tallies, and phase transitions.
    - [x] The player is never prompted to act (no speaking turn, no pointing at Night, no vote prompts), and any keyboard input other than Ctrl-C is ignored.
    - [x] If the human was Mafia, they no longer see Mafia-private messages from the point of death onward (dead Mafia lose their private channel).
    - [x] The end-of-game screen is shown to the spectator just as it would be to a living player.

### 2.9 Graceful exit and safety cap

- **As a player**, **I want to** be able to stop the game at any time and trust that it won't loop forever, **so that I** feel in control of the session.
  - **Acceptance Criteria:**
    - [x] Pressing Ctrl-C at any point — including mid-turn, mid-vote, or on the end-of-game screen — exits the game cleanly, with a short "Game aborted." message and no Python traceback on screen.
    - [x] If the game reaches 20 full Night/Day cycles without a win condition triggering, the Moderator announces a draw and shows the end-of-game screen with the note "The game ended in a draw after 20 cycles."
    - [x] The player is never stuck on a screen that does not advance, regardless of outcome.

---

## 3. Scope and Boundaries

### In-Scope

- Launching the game from the command line and prompting the player for a display name.
- A fixed starter lineup of 2 Mafia + 5 Law-abiding Citizens (7 players including the human), with randomized role assignment for the human.
- Fresh, distinct AI player names generated at the start of each run.
- Public roster introduction by the Moderator, followed by a private role reveal to the human.
- First-Night private introductions for Mafia players (human and AI).
- Single-round Night-kill mechanism: Mafia privately point at Law-abiding targets, majority selects the victim, random tie-break.
- Moderator announcements for "Night falls," "Day breaks," Night kills, Day executions, failed votes, end-of-Day, and end-of-game.
- Synchronous Day phase with round-robin speaking (random per-round order), each turn being either a short spoken line or a vote initiation.
- Vote-to-execute flow: any player can call a vote on their turn; all alive players vote Yes/No in roster order; strict majority executes; ties do not execute; up to three votes per Day; a six-round safety cap on pure talking.
- Human-in-the-loop prompts for: name entry, Night pointing (if Mafia), speaking on turn, calling votes, casting votes.
- Win-condition checks after every Night and every Day; cascading end-of-game screen with winner, chronological death log, and full roster reveal.
- Read-only spectator view after the human's character dies.
- Clean Ctrl-C exit at any time.
- 20-cycle safety cap with a draw announcement.

### Out-of-Scope

- **Configurable role counts** — this spec ships only the fixed 2M + 5C preset.
- **Multi-round Mafia consensus by pointing** — this spec uses a single round with immediate majority fallback.
- **AI character sheets and personalities** — AI players speak and vote generically in this spec.
- **Per-AI private diaries** — no diary entries are written or stored.
- **Asynchronous Day chat with rate-limited AI chatter** — Day is strictly synchronous and turn-based in this spec.
- **Concurrent human typing with live display** — the human types only on their turn, not alongside streaming AI messages.
- **Vote-opens-lock-chat transition** — trivially satisfied by the synchronous design here; the dedicated spec covers the async version.
- **Creative Moderator end-of-game recap** — the end-of-game screen is a plain text summary only.
- **Extended roles** (Detective, Protector, role-mix configuration) — Phase 4 roadmap items.
- **Save/load across sessions** — explicitly excluded in the product definition.
- **Multiple human players or networked play** — explicitly excluded in the product definition.