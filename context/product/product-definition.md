# Product Definition: Graphia

- **Version:** 1.3
- **Status:** Proposed

---

## 1. The Big Picture (The "Why")

### 1.1. Project Vision & Purpose

Graphia is a runnable console Mafia game that doubles as a personal reference implementation of modern agentic-AI patterns on AWS — multi-agent LangGraph orchestration with private per-agent state, asynchronous chat, human-in-the-loop interrupts, live streaming to a terminal UI, and **production deployment on Amazon Bedrock AgentCore** (Runtime, Gateway, per-game and long-term Memory, Observability). The north star is a short game session that is genuinely fun to play and, from a single codebase, makes each advanced LangGraph and AgentCore concept easy to locate, run, and lift into a real project.

### 1.2. Target Audience

The primary audience is the author, using Graphia as a personal reference project to explore and revisit advanced LangGraph capabilities **and Bedrock AgentCore deployment patterns**. A secondary audience is any Python developer evaluating LangGraph + AgentCore who stumbles on the repo and wants a non-trivial, end-to-end example of async multi-agent gameplay running on a managed cloud runtime with cross-session Memory.

### 1.3. User Personas

- **Persona 1: "Future Me" — the returning author**
  - **Role:** Python developer who built Graphia, now needs to recall how a specific LangGraph or AgentCore feature is wired up (e.g., per-agent private state, async message bus, consensus loop, AgentCore Gateway tool registration, AgentCore Memory writes, Runtime deployment).
  - **Goal:** Clone the repo, run it once locally and once against the deployed AgentCore Runtime, then jump to the relevant section of code (or Terraform) and lift the pattern into a real project.
  - **Frustration:** Past demos were too trivial to show real patterns, or showed framework code without ever actually deploying it anywhere.

- **Persona 2: "Curious Dev" — the drop-in reader**
  - **Role:** Python developer comfortable with LangChain basics, evaluating whether LangGraph + Bedrock AgentCore is worth adopting.
  - **Goal:** Run one command locally, see a multi-agent system work end-to-end, then run `terraform apply` and play the same game against a hosted AgentCore Runtime that retains career stats across sessions.
  - **Frustration:** Most multi-agent examples are synthetic toy tasks with no real deployment story and no observability hooks.

### 1.4. Success Metrics

- A full game (Night → Day → Night → … → end state) finishes from a single `uv run` command in **local mode** without stack traces, on the default configuration.
- A full game also finishes when launched against the **deployed AgentCore Runtime** (provisioned via the included Terraform), via `uv run python -m graphia --remote`.
- The day-chat display remains readable throughout a full Day phase: AI "typing" output and human input do not corrupt each other's lines in the terminal.
- The end-of-game Moderator recap feels satisfying — it visibly draws on the dead players' private diaries and the vote logs, and reveals information the human didn't have during play.
- Opening the source, each advertised concept — multi-agent nodes, private per-agent state, async chat coordination, human interrupts, streaming, AgentCore Runtime entrypoint, Gateway-fronted diary surface, per-game Memory reads/writes, long-term cross-session Memory reads/writes, observability traces — is locatable in a few minutes.
- After first game, the pre-game greeting and post-game career-stats panel both show accurate cumulative counts that reflect the games actually played — including the just-finished one — in both local mode (file-backed) and remote mode (AgentCore long-term Memory).

---

## 2. The Product Experience (The "What")

### 2.1. Core Features

- **Configurable game setup** — at startup the human is asked for the counts of Law-abiding Citizens and Mafiosos; roles are assigned randomly.
- **AI character generation** — a creative LLM generates a distinct personality and backstory for each AI player at the start, persisted for the whole game.
- **Night phase with private Mafia consensus** — Mafiosos are introduced to each other on the first Night via private communication from the Moderator; on every Night they choose a victim by pointing (no chat), iterating rounds until consensus or falling back to majority vote with random tie-break.
- **Day phase with asynchronous discussion** — surviving players chat in a shared day channel; the human types freely, AI players post rate-limited messages; Mafiosos pose as Citizens. Any player can *initiate a vote* to execute a specific person; once a vote is open no further chat is allowed until all alive players have voted. Majority-to-execute ends the Day; otherwise discussion resumes. Up to three votes per Day, then the Day ends regardless.
- **Live "typing" day chat with concurrent human input** — AI messages stream to the console as they're produced while the human can type their own message at the same time, without display corruption.
- **Private diaries** — before each Night, each surviving AI player writes a short diary entry under a per-player namespace; entries are visible only to the owning agent during play, and revealed in bulk to the Moderator at end-of-game. In remote mode the diary store is AgentCore Memory (read/write through an AgentCore Gateway-fronted surface); in local mode the diaries live in the game's own state.
- **End-of-game Moderator story** — when a win condition is met, the Moderator pulls the diaries of all dead players plus the day-chat logs and the night-kill vote logs, and delivers a short creative recap that reveals the hidden twists.
- **Win conditions** — Law-abiding win when all Mafia are eliminated; Mafia win when their count equals or exceeds the Law-abiding count.
- **Bedrock AgentCore deployment** — a Terraform module provisions an AgentCore Runtime hosting the game agent, an AgentCore Gateway exposing the per-game diary read/write surface over MCP, an AgentCore Memory store used both for per-game diaries and for long-term cross-game stats, and AgentCore observability emitting traces to CloudWatch.
- **Cross-game career and aggregate statistics** — across game sessions, Graphia accumulates counts of night-kill initiations and votes, day-execution initiations and votes, game outcomes, and human-player career stats by role (Mafia / Law-abiding). The pre-game opener greets the human with a one-paragraph cumulative summary; the post-game wrap-up appends a career-stats panel showing the updated career numbers and the deltas from the just-finished game. In remote mode the long-term store is **AgentCore Memory** (the explicit demonstration of long-term cross-session Memory); in local mode the same data lives in a file in the game's local data directory.
- **Local mode retained for dev** — the game can still run fully locally for game-mechanics development, with no AgentCore (Runtime, Gateway, or Memory) calls. The LLM is reached through whichever provider is configured at the time — Bedrock at v1.1; the future Ollama provider on the roadmap is what enables truly offline play. A single flag toggles between local and remote (AgentCore-hosted) execution.

### 2.2. User Journey

The human first runs `terraform apply` from the included module to provision the AgentCore Runtime, Gateway, and Memory store. They then launch the game from the terminal — either `uv run python -m graphia` for local mode (game mechanics development) or `uv run python -m graphia --remote` to invoke the deployed AgentCore Runtime. On launch — before the role-count prompts — the game greets them with a one-paragraph career summary drawn from the cross-game stats store: how many games they've played, win rate by role, kills attempted vs. successful, votes initiated, etc. (empty on the very first run). They then answer a couple of prompts for the number of Law-abiding Citizens and Mafiosos, and watch the Moderator announce the opening. The game begins with Night: if the human drew Mafia, the Moderator privately introduces them to their teammates and walks them through a pointing-based consensus on a target; if not, they see only a brief "night falls" message. Morning arrives, the Moderator announces who was killed, and the Day chat opens. AI players post messages over time; the human can type their own messages concurrently. At any point any player may call a vote to execute someone, at which point the chat locks and everyone votes. Days alternate with Nights — with each AI writing a fresh diary before nightfall — until a win condition triggers, after which the Moderator delivers a final creative recap drawing on each dead player's stored diary. The recap is followed by a brief career-stats panel that updates the cumulative numbers and shows the deltas from this game (e.g., *"You initiated 1 day-vote today — career total: 6"*).

---

## 3. Project Boundaries

### 3.1. What's In-Scope for this Version

- Console-only interface, single process on the client side, one human player per game.
- Exactly two roles in play: Law-abiding Citizen and Mafioso.
- User-configurable counts of each role at startup.
- AI character sheet generation at game start via a creative LLM.
- First-night private Mafia introductions by the Moderator.
- Night-kill consensus loop with round cap and majority-vote fallback (random tie-break).
- Asynchronous Day chat with per-AI rate limiting, concurrent human input, and live streaming display.
- Vote-to-execute flow: any player can initiate; chat locks; everyone votes; majority kills the target or Day continues; three-vote cap per Day.
- Per-AI private diaries written before each Night and persisted (in AgentCore Memory in remote mode, in-process state in local mode) until end of game.
- End-of-game Moderator recap that reads diaries and logs directly and uses them to tell a creative story with twists.
- LangGraph implementation with clearly identifiable multi-agent, private-state, interrupt, and streaming constructs.
- **Bedrock AgentCore Runtime** hosting the game agent, deployable via the included Terraform module.
- **Bedrock AgentCore Gateway** registering the per-game diary read/write surface and exposing it over MCP for the owning agents to call. (The richer AI-player and Moderator tool surface is deferred to Phase 7 per the roadmap.)
- **Bedrock AgentCore Memory** as both the per-game diary store and the long-term cross-game stats store in remote mode.
- **AgentCore observability** emitting traces to CloudWatch for inspection.
- Cross-game career and aggregate statistics — night-kill initiations and votes, day-execution initiations and votes, game outcomes, and per-role breakdowns accumulated across all played sessions.
- Pre-game career-summary greeting on launch, and post-game career-stats panel after the Moderator recap.
- Local-mode cross-game stats persistence via a file in the game's local data directory (separate from the in-process LangGraph state used for diaries and game-state).
- **Local mode** preserved as a first-class run path for game-mechanics development (no AgentCore calls; tools resolved in-process; diaries held in LangGraph state; cross-game stats persisted to a local file).

### 3.2. What's Out-of-Scope (Non-Goals)

- Additional roles (Detective, Protector, etc.) — explicitly deferred to future versions.
- GUI, web UI, or non-console front ends.
- Multi-human or networked multiplayer; no remote players.
- Persistent storage of *full* game transcripts, diaries, or vote-by-vote replays across separate game sessions — only game stats summaries (the data needed for the career-stats and aggregate views) are persisted to the cross-game store. Each in-progress game is still a fresh LangGraph thread; no save/load of in-progress games.
- Rich AI tool-use during the Day phase (investigation tool, evidence-builder tool) and structured Moderator helper tools (kill-log summary, diary fetch, recap-input assembly) — deferred to Phase 7 further-improvement possibilities (per CR 002 amendment, applying the *design-driven-by-realistic-needs* principle: Mafia game-design cases for these tools are mostly degenerate vs. structured output). The v1.x Moderator end-of-game recap reads state directly rather than via tool calls. The Gateway-fronted diary read/write surface remains in scope as the v1.x AgentCore Gateway demonstration.
- Web search / external research tools for AI players or the Moderator — all in-game data access reads game state only, keeping the game self-contained and deterministic to reason about.
- Tutorial document, narrated walkthrough, or per-concept commentary beyond what lives in the source and the Terraform module.
- Tooling beyond the game itself (leaderboards, replays, analytics, packaging for distribution).
  - A dedicated `graphia stats` standalone CLI command — career and aggregate stats surface only via the pre-game greeting and the post-game panel within a normal game launch.
