# Product Roadmap: Graphia

_This roadmap outlines our strategic direction based on customer needs and business goals. It focuses on the "what" and "why," not the technical "how."_

---

### Phase 1

_A true skeleton: a playable end-to-end game on a fixed preset lineup, with the simplest possible resolution rules. The goal is to prove the core loop works before layering on flexibility or richer behavior._

- [x] **Preset Game Lifecycle**
  - [x] **Fixed Starter Lineup:** Ship with a single hard-coded lineup (2 Mafiosos vs. 5 Law-abiding Citizens — 7 players total with the human as one of them) so new runs start instantly without setup prompts and the game is reasonably balanced toward the Law-abiding side.
  - [x] **Moderator & Phase Alternation:** Establish a Moderator voice that announces phase transitions and alternates Night → Day → Night until a win condition triggers, giving the game its rhythm.
  - [x] **Win Condition Detection & Endgame:** Detect the Law-abiding win (all Mafia eliminated) and the Mafia win (Mafia count ≥ Law-abiding count), stop the loop, and close the game.

- [x] **Core Gameplay Loop (Simplest Rules)**
  - [x] **Single-Round Night Kill by Majority Vote:** Resolve each Night with one round of anonymous pointing: tally votes, kill the top target, random tie-break if needed — no multi-round consensus yet.
  - [x] **First-Night Mafia Introductions:** On the first Night, have the Moderator privately tell each Mafioso who their teammates are so the human Mafioso (if drawn) can actually play their role.
  - [x] **Day Discussion & Vote-to-Execute (Synchronous):** In a turn-based Day phase, let any player speak once per round and any player call a vote; when a vote is open, chat locks, all survivors vote, and majority executes. Allow up to three votes per Day, then end the Day regardless.
  - [x] **Human-in-the-Loop Turns:** Use interrupt/resume so the human can point at Night, speak during the Day, initiate votes, and cast votes, without being skipped.

---

### Phase 2

_Hard scope for v1.1. The reference project must demonstrate Bedrock AgentCore as a real production deployment target — not just a future possibility — while keeping a no-AWS local mode available for game-mechanics development._

- [x] **Hosted AgentCore Deployment**
  - [x] **Bedrock AgentCore Runtime Hosting:** Package Graphia's game-engine core as a Bedrock AgentCore Runtime workload so a full game can be played end-to-end against a hosted runtime, not just on a laptop. This is the headline demonstration of AgentCore as a managed agent runtime.
  - [x] **AgentCore Gateway-Fronted Tool Surface:** Register the in-game tools (the per-game diary read/write surface, plus any future game-state tools) with AgentCore Gateway and reach them over MCP, so the agents in the hosted runtime call tools through the same governance layer a real product would.
  - [x] **AgentCore Memory for Per-Game State:** Use AgentCore Memory in remote mode as the per-game diary store, with each diary scoped to its owning agent's namespace and the game's lifetime.
  - [x] **AgentCore Observability:** Emit traces from the hosted runtime — a navigable per-session trace tree — so a player or operator can inspect what the agents did during a game.
  - [x] **Terraform Provisioning:** Ship an infrastructure-as-code package that stands up the Runtime + Gateway + Memory + Observability set with one command.
  - [x] **Local Mode Preserved:** Keep `uv run python -m graphia` runnable with no AgentCore calls (tools resolved in-process, diaries held in the game's own state) so game-mechanics development continues to work offline-of-AgentCore.

---

### Phase 3

_Hard scope for v1.2. Demonstrates the long-term, cross-session use of AgentCore Memory — a distinct pattern from the per-game Memory use in Phase 2 — by accumulating career and aggregate stats across game sessions._

- [ ] **Long-Term Cross-Game Memory & Career Stats**
  - [ ] **Cross-Game Stats Accumulation:** Persist a small end-of-game summary from each game (night-kill initiations and votes, day-execution initiations and votes, game outcomes, role-broken-down counts) so the player builds a real career history across many games.
  - [ ] **Pre-Game Career-Summary Greeting:** On launch, before the role-count prompts, greet the player with a one-paragraph cumulative summary drawn from the cross-game store (games played, win rate by role, kills attempted vs. successful, etc.). Empty on the very first run.
  - [ ] **Post-Game Career-Stats Panel:** After the Moderator's end-of-game recap, append a brief panel that shows the updated career numbers and the deltas from the just-finished game.
  - [ ] **AgentCore Memory as the Long-Term Store (Remote Mode):** In remote mode, persist the cross-game stats summaries via AgentCore Memory — the explicit demonstration of long-term cross-session Memory.
  - [ ] **Local-File Stats Store (Local Mode):** In local mode, persist the same data shape to a file in the game's local data directory so dev work without AWS still sees stats accumulate. (Note: this is the only persistent state that crosses sessions in local mode; full game transcripts, diaries, and replays remain non-persistent.)

---

### Phase 4

_With AgentCore deployment and long-term Memory in place, give players a real choice in how the game reaches the LLM — so the project meets each user's access and budget situation._

- [ ] **AI Provider Flexibility**
  - [ ] **AWS Profile / SSO Credentials:** Let the player run Graphia against standard AWS credentials (named profile, SSO-backed AssumeRole, environment variables) — the same identity already used for other corporate AWS tooling — as an alternative to a Bedrock bearer token. Makes the game usable by engineers who already have an AWS profile but no separate workshop bearer.
  - [ ] **Local Ollama Provider:** Add support for running Graphia entirely against a local Ollama-served model, so a contributor with no cloud access (or who simply doesn't want to spend on cloud inference) can develop, demo, and play offline at zero per-token cost.

---

### Phase 5

_Add the configurability and richer consensus mechanics the product definition calls for, now that the deployment, memory, and provider foundations are in place._

- [ ] **Setup Flexibility**
  - [ ] **Configurable Role Counts:** Replace the fixed lineup with startup prompts asking the human for the number of Law-abiding Citizens and Mafiosos, then randomly assign roles so every run starts fresh.

- [ ] **Richer Night Resolution**
  - [ ] **Multi-Round Mafia Consensus by Pointing:** Have Mafiosos converge on a victim across multiple rounds of private pointing, falling back to the Phase 1 single-round majority-with-random-tie-break only if they fail to agree within a round cap.

---

### Phase 6

_Once the game mechanics are solid, layer in the features that make Graphia feel alive and showcase the advanced LangGraph patterns the project is really about._

- [ ] **AI Personas & Per-Game Memory**
  - [ ] **AI Character Sheet Generation:** At game start, have a creative LLM produce a distinct personality, backstory, and voice for each AI player, persisted for the whole game so their behavior feels consistent.
  - [ ] **Per-AI Private Diaries:** Before each Night, have every surviving AI player write a short private diary entry capturing their suspicions and plans, kept hidden during play and surfaced at the end. Stored in **AgentCore Memory** (remote mode) under a per-player namespace, or in the game's own state (local mode) — exercising the per-game AgentCore Memory pattern from Phase 2.

- [ ] **Asynchronous Day Chat**
  - [ ] **Rate-Limited Concurrent AI Chatter:** Replace the synchronous Day loop with an asynchronous one in which AI players post messages over time, subject to a per-player rate limit, so discussions feel like a real room rather than a round-robin.
  - [ ] **Concurrent Human Typing with Live Display:** Render AI messages token-by-token in the terminal while the human can type their own message at the same time, without lines colliding or corrupting each other.
  - [ ] **Vote-Opens-Lock-Chat Handoff:** When any player calls a vote mid-conversation, cleanly freeze the chat mid-stream and transition all players (human and AI) into voting mode until the ballot resolves.

- [ ] **End-of-Game Payoff**
  - [ ] **Moderator Creative Recap:** When a win condition triggers, feed the Moderator the dead players' diaries, the day-chat logs, and the night-kill vote logs, and have them deliver a short creative story that reveals hidden twists the human couldn't see during play. (At this phase the Moderator reads the inputs directly; the structured tool-call surface for the same data is a Phase 7 further-improvement item.)

---

### Phase 7

_Further improvement possibilities — explicitly deferred from v1, genuinely aspirational. Priority and scope may be refined based on what we learn from earlier phases; the reference project is shippable without these._

- [ ] **AI Tool-Use Demonstration**
  - [ ] **Day-Phase Investigation Tool:** Let AI players call an investigation tool mid-Day to surface a target's prior public statements and vote record, grounding their next utterance in concrete data instead of free-form speculation.
  - [ ] **Day-Phase Evidence-Builder Tool:** Let AI players call an evidence-builder tool to compile a structured case against a suspect from the day-chat and night-kill logs, with the result feeding their accusation.
  - [ ] **Visible Tool-Call Beats in Chat:** Render tool calls as short, in-line beats in the day chat ("Alice consults her notes…") so the human can see when an AI is reasoning from data rather than improvising.
  - [ ] **Moderator Helper Tools:** Replace the Moderator's direct state reads with structured tool calls (kill-log summary, diary fetch, recap-input assembly) so the Moderator's mechanical work is also a tool-use demonstration.

- [ ] **Expanded Role Roster**
  - [ ] **Detective Role:** Introduce a Detective who can privately investigate one player per Night to learn their true alignment.
  - [ ] **Protector Role:** Introduce a Protector (e.g., Doctor/Bodyguard) who can privately shield one player per Night from the Mafia kill.
  - [ ] **Role-Mix Configuration:** Let the human configure which extended roles are in play and in what numbers at startup, maintaining game balance.
