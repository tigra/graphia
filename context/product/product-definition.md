# Product Definition: Graphia

- **Version:** 1.0
- **Status:** Proposed

---

## 1. The Big Picture (The "Why")

### 1.1. Project Vision & Purpose

Graphia is a runnable console Mafia game that doubles as a personal reference implementation of modern LangGraph patterns — multi-agent orchestration with private per-agent state, asynchronous chat, human-in-the-loop interrupts, and live streaming to a terminal UI. The north star is a short game session that is genuinely fun to play and, from a single codebase, makes each advanced LangGraph concept easy to locate and understand later.

### 1.2. Target Audience

The primary audience is the author, using Graphia as a personal reference project to explore and revisit advanced LangGraph capabilities. A secondary audience is any LangGraph-curious Python developer who stumbles on the repo and wants a non-trivial, end-to-end example of async multi-agent gameplay.

### 1.3. User Personas

- **Persona 1: "Future Me" — the returning author**
  - **Role:** Python developer who built Graphia, now needs to recall how a specific LangGraph feature is wired up (e.g., per-agent private state, async message bus, consensus loop).
  - **Goal:** Clone the repo, run it once to confirm it still works, then jump to the relevant section of code and lift the pattern into a real project.
  - **Frustration:** Past demos were too trivial to show real patterns, or too sprawling to read in one sitting.

- **Persona 2: "Curious Dev" — the drop-in reader**
  - **Role:** Python developer comfortable with LangChain basics, evaluating whether LangGraph is worth adopting.
  - **Goal:** Run one command, see a multi-agent system work end-to-end, and read the source to understand how the pieces fit.
  - **Frustration:** Most multi-agent examples are synthetic toy tasks; they want to see interrupts, private state, and async streaming in something with actual game logic.

### 1.4. Success Metrics

- A full game (Night → Day → Night → … → end state) finishes from a single `uv run` command without stack traces, on the default configuration.
- The day-chat display remains readable throughout a full Day phase: AI "typing" output and human input do not corrupt each other's lines in the terminal.
- The end-of-game Moderator recap feels satisfying — it visibly draws on the dead players' private diaries and the vote logs, and reveals information the human didn't have during play.
- Opening the source, each advertised LangGraph concept (multi-agent nodes, private per-agent state, async chat coordination, human interrupts, streaming) is locatable in a few minutes.

---

## 2. The Product Experience (The "What")

### 2.1. Core Features

- **Configurable game setup** — at startup the human is asked for the counts of Law-abiding Citizens and Mafiosos; roles are assigned randomly.
- **AI character generation** — a creative LLM generates a distinct personality and backstory for each AI player at the start, persisted for the whole game.
- **Night phase with private Mafia consensus** — Mafiosos are introduced to each other on the first Night via private communication from the Moderator; on every Night they choose a victim by pointing (no chat), iterating rounds until consensus or falling back to majority vote with random tie-break.
- **Day phase with asynchronous discussion** — surviving players chat in a shared day channel; the human types freely, AI players post rate-limited messages; Mafiosos pose as Citizens. Any player can *initiate a vote* to execute a specific person; once a vote is open no further chat is allowed until all alive players have voted. Majority-to-execute ends the Day; otherwise discussion resumes. Up to three votes per Day, then the Day ends regardless.
- **Live "typing" day chat with concurrent human input** — AI messages stream to the console as they're produced while the human can type their own message at the same time, without display corruption.
- **Private diaries** — before each Night, each surviving AI player writes a short diary entry only they can see during play.
- **End-of-game Moderator story** — when a win condition is met, the Moderator receives the diaries of all dead players plus the day-chat logs and the night-kill vote logs, and delivers a short creative recap that reveals the hidden twists.
- **Win conditions** — Law-abiding win when all Mafia are eliminated; Mafia win when their count equals or exceeds the Law-abiding count.

### 2.2. User Journey

The human launches the game from the terminal, answers a couple of prompts for the number of Law-abiding Citizens and Mafiosos, and watches the Moderator announce the opening. The game begins with Night: if the human drew Mafia, the Moderator privately introduces them to their teammates and walks them through a pointing-based consensus on a target; if not, they see only a brief "night falls" message. Morning arrives, the Moderator announces who was killed, and the Day chat opens. AI players post messages over time while the human can type their own messages concurrently; at any point any player may call a vote to execute someone, at which point the chat locks and everyone votes. Days alternate with Nights until a win condition triggers, after which the Moderator delivers a final creative recap drawing on each dead player's private diary.

---

## 3. Project Boundaries

### 3.1. What's In-Scope for this Version

- Console-only interface, single process, one human player per game.
- Exactly two roles in play: Law-abiding Citizen and Mafioso.
- User-configurable counts of each role at startup.
- AI character sheet generation at game start via a creative LLM.
- First-night private Mafia introductions by the Moderator.
- Night-kill consensus loop with round cap and majority-vote fallback (random tie-break).
- Asynchronous Day chat with per-AI rate limiting, concurrent human input, and live streaming display.
- Vote-to-execute flow: any player can initiate; chat locks; everyone votes; majority kills the target or Day continues; three-vote cap per Day.
- Per-AI private diaries written before each Night and persisted until end of game.
- End-of-game Moderator recap that uses diaries and logs to tell a creative story with twists.
- LangGraph implementation with clearly identifiable multi-agent, private-state, interrupt, and streaming constructs.

### 3.2. What's Out-of-Scope (Non-Goals)

- Additional roles (Detective, Protector, etc.) — explicitly deferred to future versions.
- GUI, web UI, or non-console front ends.
- Multi-human or networked multiplayer; no remote players.
- Persistent storage of game history across sessions; no save/load; no database.
- Tutorial document, narrated walkthrough, or per-concept commentary beyond what lives in the source.
- Tooling beyond the game itself (leaderboards, replays, analytics, packaging for distribution).