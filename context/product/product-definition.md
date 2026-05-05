# Product Definition: Graphia

- **Version:** 1.1
- **Status:** Proposed

---

## 1. The Big Picture (The "Why")

### 1.1. Project Vision & Purpose

Graphia is a runnable console Mafia game that doubles as a personal reference implementation of modern agentic-AI patterns on AWS — multi-agent LangGraph orchestration with private per-agent state, asynchronous chat, human-in-the-loop interrupts, live streaming to a terminal UI, **LangGraph tool-use by AI players**, and **production deployment on Amazon Bedrock AgentCore** (Runtime, Gateway, Memory, Observability). The north star is a short game session that is genuinely fun to play and, from a single codebase, makes each advanced LangGraph and AgentCore concept easy to locate, run, and lift into a real project.

### 1.2. Target Audience

The primary audience is the author, using Graphia as a personal reference project to explore and revisit advanced LangGraph capabilities **and Bedrock AgentCore deployment patterns**. A secondary audience is any Python developer evaluating LangGraph + AgentCore who stumbles on the repo and wants a non-trivial, end-to-end example of async multi-agent gameplay where AI agents actually call tools and run on a managed cloud runtime.

### 1.3. User Personas

- **Persona 1: "Future Me" — the returning author**
  - **Role:** Python developer who built Graphia, now needs to recall how a specific LangGraph or AgentCore feature is wired up (e.g., per-agent private state, async message bus, consensus loop, AgentCore Gateway tool registration, AgentCore Memory writes, Runtime deployment).
  - **Goal:** Clone the repo, run it once locally and once against the deployed AgentCore Runtime, then jump to the relevant section of code (or Terraform) and lift the pattern into a real project.
  - **Frustration:** Past demos were too trivial to show real patterns, or showed framework code without ever actually deploying it anywhere.

- **Persona 2: "Curious Dev" — the drop-in reader**
  - **Role:** Python developer comfortable with LangChain basics, evaluating whether LangGraph + Bedrock AgentCore is worth adopting.
  - **Goal:** Run one command locally, see a multi-agent system work end-to-end with real tool calls, then run `terraform apply` and play the same game against a hosted AgentCore Runtime.
  - **Frustration:** Most multi-agent examples are synthetic toy tasks with no tools, no deployment story, and no observability hooks.

### 1.4. Success Metrics

- A full game (Night → Day → Night → … → end state) finishes from a single `uv run` command in **local mode** without stack traces, on the default configuration.
- A full game also finishes when launched against the **deployed AgentCore Runtime** (provisioned via the included Terraform), via `uv run python -m graphia --remote`.
- The day-chat display remains readable throughout a full Day phase: AI "typing" output, tool-call beats, and human input do not corrupt each other's lines in the terminal.
- AI tool calls (investigation, evidence-building) visibly influence Day-phase decisions — the recap can point to at least one accusation that was grounded in a tool result.
- The end-of-game Moderator recap feels satisfying — it visibly draws on the dead players' private diaries (read via AgentCore Memory) and the vote logs, and reveals information the human didn't have during play.
- Opening the source, each advertised concept — multi-agent nodes, private per-agent state, async chat coordination, human interrupts, streaming, LangGraph tool calls, AgentCore Runtime entrypoint, Gateway-registered tools, Memory reads/writes, observability traces — is locatable in a few minutes.

---

## 2. The Product Experience (The "What")

### 2.1. Core Features

- **Configurable game setup** — at startup the human is asked for the counts of Law-abiding Citizens and Mafiosos; roles are assigned randomly.
- **AI character generation** — a creative LLM generates a distinct personality and backstory for each AI player at the start, persisted for the whole game.
- **Night phase with private Mafia consensus** — Mafiosos are introduced to each other on the first Night via private communication from the Moderator; on every Night they choose a victim by pointing (no chat), iterating rounds until consensus or falling back to majority vote with random tie-break.
- **Day phase with asynchronous discussion** — surviving players chat in a shared day channel; the human types freely, AI players post rate-limited messages; Mafiosos pose as Citizens. Any player can *initiate a vote* to execute a specific person; once a vote is open no further chat is allowed until all alive players have voted. Majority-to-execute ends the Day; otherwise discussion resumes. Up to three votes per Day, then the Day ends regardless.
- **AI tool use during Day phase** — AI players can call tools mid-discussion to ground their statements: an **investigation tool** that returns a target player's prior public statements and vote record, and an **evidence-builder tool** that compiles a structured case against a suspect from the day-chat and night-kill logs. Tool calls are visible as short beats in the chat ("Alice consults her notes…") so the human can see when an AI is reasoning from data.
- **Moderator tools** — the Moderator uses tools (not free-form LLM calls) for mechanical work: summarising the kill log, fetching dead players' diaries from Memory, and assembling the end-of-game recap inputs.
- **Live "typing" day chat with concurrent human input** — AI messages stream to the console as they're produced while the human can type their own message at the same time, without display corruption.
- **Private diaries via AgentCore Memory** — before each Night, each surviving AI player writes a short diary entry into AgentCore Memory under a per-player namespace; entries are queryable as a tool by the owning agent, and revealed in bulk to the Moderator at end-of-game.
- **End-of-game Moderator story** — when a win condition is met, the Moderator pulls the diaries of all dead players plus the day-chat logs and the night-kill vote logs, and delivers a short creative recap that reveals the hidden twists.
- **Win conditions** — Law-abiding win when all Mafia are eliminated; Mafia win when their count equals or exceeds the Law-abiding count.
- **Bedrock AgentCore deployment** — a Terraform module provisions an AgentCore Runtime hosting the game agent, an AgentCore Gateway exposing the in-game tools (investigation, evidence-builder, Moderator helpers, diary read/write) over MCP, an AgentCore Memory store for diaries and per-player history, and AgentCore observability emitting traces to CloudWatch.
- **Local mode retained for dev** — the game can still run fully locally (no AWS calls beyond Bedrock model invocation) for game-mechanics development and offline play; a single flag toggles between local and remote (AgentCore-hosted) execution.

### 2.2. User Journey

The human first runs `terraform apply` from the included module to provision the AgentCore Runtime, Gateway, and Memory store. They then launch the game from the terminal — either `uv run python -m graphia` for local mode (game mechanics development) or `uv run python -m graphia --remote` to invoke the deployed AgentCore Runtime. They answer a couple of prompts for the number of Law-abiding Citizens and Mafiosos, and watch the Moderator announce the opening. The game begins with Night: if the human drew Mafia, the Moderator privately introduces them to their teammates and walks them through a pointing-based consensus on a target; if not, they see only a brief "night falls" message. Morning arrives, the Moderator announces who was killed, and the Day chat opens. AI players post messages over time, occasionally pausing to call an investigation or evidence-building tool whose result feeds their next utterance; the human can type their own messages concurrently. At any point any player may call a vote to execute someone, at which point the chat locks and everyone votes. Days alternate with Nights — with each AI writing a fresh diary into AgentCore Memory before nightfall — until a win condition triggers, after which the Moderator delivers a final creative recap drawing on each dead player's stored diary.

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
- LangGraph tool calls by AI players: investigation tool (read prior statements / votes), evidence-builder tool (assemble structured accusation).
- LangGraph tool calls by the Moderator for mechanical work (kill-log summary, diary fetch, recap assembly).
- End-of-game Moderator recap that uses diaries and logs to tell a creative story with twists.
- LangGraph implementation with clearly identifiable multi-agent, private-state, interrupt, streaming, and tool-use constructs.
- **Bedrock AgentCore Runtime** hosting the game agent, deployable via the included Terraform module.
- **Bedrock AgentCore Gateway** registering each in-game tool and exposing them over MCP for the agents to call.
- **Bedrock AgentCore Memory** as the per-player diary / history store in remote mode.
- **AgentCore observability** emitting traces to CloudWatch for inspection.
- **Local mode** preserved as a first-class run path for game-mechanics development (no AgentCore calls; tools resolved in-process; diaries held in LangGraph state).

### 3.2. What's Out-of-Scope (Non-Goals)

- Additional roles (Detective, Protector, etc.) — explicitly deferred to future versions.
- GUI, web UI, or non-console front ends.
- Multi-human or networked multiplayer; no remote players.
- Persistent storage of game history *across separate game sessions*; each game is a fresh thread, and AgentCore Memory namespaces are scoped to a single game's lifetime — no save/load of past games.
- Web search / external research tools for AI players or the Moderator — all in-game tools read game state only, keeping the game self-contained and deterministic to reason about.
- Tutorial document, narrated walkthrough, or per-concept commentary beyond what lives in the source and the Terraform module.
- Tooling beyond the game itself (leaderboards, replays, analytics, packaging for distribution).
