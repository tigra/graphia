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

- [x] **Long-Term Cross-Game Memory & Career Stats**
  - [x] **Cross-Game Stats Accumulation:** Persist a small end-of-game summary from each game (night-kill initiations and votes, day-execution initiations and votes, game outcomes, role-broken-down counts) so the player builds a real career history across many games.
  - [x] **Pre-Game Career-Summary Greeting:** On launch, before the role-count prompts, greet the player with a one-paragraph cumulative summary drawn from the cross-game store (games played, win rate by role, kills attempted vs. successful, etc.). Empty on the very first run.
  - [x] **Post-Game Career-Stats Panel:** After the Moderator's end-of-game recap, append a brief panel that shows the updated career numbers and the deltas from the just-finished game.
  - [x] **AgentCore Memory as the Long-Term Store (Remote Mode):** In remote mode, persist the cross-game stats summaries via AgentCore Memory — the explicit demonstration of long-term cross-session Memory.
  - [x] **Local-File Stats Store (Local Mode):** In local mode, persist the same data shape to a file in the game's local data directory so dev work without AWS still sees stats accumulate. (Note: this is the only persistent state that crosses sessions in local mode; full game transcripts, diaries, and replays remain non-persistent.)

---

### Phase 4

_With AgentCore deployment and long-term Memory in place, give players a real choice in how the game reaches the LLM — so the project meets each user's access and budget situation._

- [x] **AI Provider Flexibility**
  - [x] **AWS Profile / SSO Credentials:** Let the player run Graphia against standard AWS credentials (named profile, SSO-backed AssumeRole, environment variables) — the same identity already used for other corporate AWS tooling — as an alternative to a Bedrock bearer token. Makes the game usable by engineers who already have an AWS profile but no separate workshop bearer. *(Shipped incrementally across the Phase 2–3 deploy/runtime work; SSO has been the canonical Bedrock auth path since 2026-05 — see architecture §3.)*
  - [x] **Local Ollama Provider:** Add support for running Graphia entirely against a local Ollama-served model, so a contributor with no cloud access (or who simply doesn't want to spend on cloud inference) can develop, demo, and play offline at zero per-token cost. *(Spec 010 — verified Completed 2026-06-12; ADRs 009/010.)*

---

### Phase 5

_Add the configurability and richer consensus mechanics the product definition calls for, now that the deployment, memory, and provider foundations are in place._

- [x] **Setup Flexibility**
  - [x] **Configurable Role Counts:** Replace the fixed lineup with the way for a human to specify numbers of Law-abiding Citizens and Mafiosos, then randomly assign roles so every run starts fresh. *(Spec 014, Completed 2026-06-16.)*

- [x] **Richer Night Resolution**
  - [x] **Multi-Round Mafia Consensus by Pointing:** Have Mafiosos converge on a victim across multiple rounds of private pointing, falling back to the Phase 1 single-round majority-with-random-tie-break only if they fail to agree within a round cap. *(Spec 015, Completed 2026-06-17.)*

---

### Phase 6

_Once the game mechanics are solid, layer in the features that make Graphia feel alive and showcase the advanced LangGraph patterns the project is really about. The near-term focus is **Day decisiveness and per-AI reasoning** — making the AI town actually act on what it knows: the n=10 ollama review found the new day-round recap accurate at every round yet never acted upon, and the town still wins 0/10._

- [x] **Eval Transcript Preservation**
  - [x] **Preserved, Browsable Eval Transcripts:** Persist the **full game transcript of each measured (eval) run** — a narrow, **eval-only** exception to the otherwise-standing "transcripts are non-persistent across sessions" rule — and make it browsable from the eval-ledger viewer, next to that run's recorded metrics. Valuable on its own for **human evaluation** (reading what the AI actually said in a measured game, not just the numbers), and it is also the substrate the LLM-as-Judge (Phase 7) later reads. (Separated from the judge because the transcripts are useful to a human reviewer with or without an automated judge — useful *now*, given the open AI-quality questions.)

- [x] **Day-Round Moderator Recap**
  - [x] **End-of-Round Day-Dynamics Nudge:** At the end of each Day round, have the Moderator post a brief **public** status to all players — the day number, how many Law-abiding Citizens and Mafiosos are still alive, how many votes were initiated this Day, and who was executed this Day (by side) — so everyone shares one clear read of where the game stands. This **surfaces facts already derivable from public play** (executed players' roles are revealed; night victims are always Law-abiding) into a single shared picture rather than disclosing anything hidden. The recap is woven into the AI players' working context **in chronological order**, alongside the day's utterances and other events, so it informs their later speech and votes the same way an utterance does. (A candidate aid for the standing **town-coordination** weakness — the AI town has never won at n=20 — by giving players a clear, common situational summary to reason from.)

- [x] **Recap-Driven Day Decisiveness**
  - [x] **Feed the Round Recap into AI Reasoning:** Surface the latest day-round recap **directly** in each surviving AI player's Day-speech and vote prompts — not only in the scrolling chat history — so they reason and act on the standing "N Law-abiding vs M Mafia" picture. The n=10 ollama review found the recap accurate at every round yet never acted upon (the town still wins 0/10), so this turns the shared situational summary into an actual decision input. (The day-decisiveness lever folded in now; the complementary force-a-vote and rule-awareness levers stay in the backlog.)
  - [x] **Game-Time in the Recap:** Add the current point in game time to the recap — **the round within the Day, beside the day number** — so every player has a clear sense of how far the Day has progressed and the mounting pressure to act before it ends.

- [x] **Browsable-Transcript Round Labels**
  - [x] **Per-Round Transcript Labels:** Label each engine speaking-round in the preserved eval transcripts. Today a single "Round" block spans several real rounds, which misreads the game's structure for a human reviewer — and would mislead the Phase-7 LLM-as-Judge that reads these transcripts. Each round (and each Moderator recap within it) should be attributable to its true round number.

- [ ] **AI Personas & Per-Game Memory**
  - [x] **AI Character Sheet Generation:** At game start, have a creative LLM produce a distinct personality, backstory, and voice for each AI player, persisted for the whole game so their behavior feels consistent. *(Spec 016 — AI Character Personas; Completed 2026-06-18. Extended in scope: a Mafioso's two-layer persona (true self + public legend) and an end-of-game reveal.)*
  - [x] **Per-AI Day-Round Private Thoughts:** At the end of each Day round, let every surviving AI player privately reflect — a short note seen by no one else (not the other players, not the human) — where it takes stock of the conversation and the game so far and plans its own strategy. These thoughts accumulate and are fed back to *that same* AI, in event order, in its later Day-speech prompts and, for a Mafioso, during Night pointing — a running private train of thought that grounds its next move. The reflection prompt is deliberately **mild**: it invites the player to think, without steering it toward any particular strategy. (A within-game working scratchpad fed back into the AI's own prompts — distinct from the **Per-AI Private Diaries** below, the before-Night entries surfaced at end-of-game.)
  - [ ] **Per-AI Private Diaries:** Before each Night, have every surviving AI player write a short private diary entry capturing their suspicions and plans, kept hidden during play and surfaced at the end. Stored in **AgentCore Memory** (remote mode) under a per-player namespace, or in the game's own state (local mode) — exercising the per-game AgentCore Memory pattern from Phase 2.

---

### Phase 6a

_The richer "feels-alive" Day experience and the end-of-game payoff — split out from Phase 6 and deferred behind the near-term Day-decisiveness / per-AI-reasoning work above._

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

- [ ] **Evidence Citation**
  - [ ] **Day-Phase Evidence Tool:** Let **any player** (human or AI), during the Day, build a *public* case by citing specific moments from the game's recorded history — a night kill, an execution, a vote cast or a vote initiated by a named player, or something a player said — each quoted **with the events immediately before and after it** for context. The citation is shown to the whole table. A single use can surface only a **bounded amount of history**, so the player must choose which moments matter; **overlapping citations share their surrounding context** (so adjacent moments cost less of the budget than scattered ones); and the affordance is **rationed to a small number of uses per Day**. (A structured, budgeted cite-specific-events mechanic available to **all** players — complementary to the AI **Day-Phase Evidence-Builder Tool** above, which is an AI free-form case-compilation.)

- [ ] **Expanded Role Roster**
  - [ ] **Detective Role:** Introduce a Detective who can privately investigate one player per Night to learn their true alignment.
  - [ ] **Protector Role:** Introduce a Protector (e.g., Doctor/Bodyguard) who can privately shield one player per Night from the Mafia kill.
  - [ ] **Role-Mix Configuration:** Let the human configure which extended roles are in play and in what numbers at startup, maintaining game balance.

- [ ] **LLM-as-Judge Game-Quality Evaluation**
  - [ ] **Whole-Transcript Judge:** Have a strong external model (Claude Opus 4.8, distinct from the gameplay model) read a *complete* game transcript — the preserved eval transcripts (see **Eval Transcript Preservation** in Phase 6) — and rate the game along several dimensions — **correctness** (were the rules and roles played out without violations?), **gameplay quality**, and — more tentatively — how **fun** the game was — emitting for each both a **numeric rating** and a **textual justification**. The judgement runs as part of the eval suite and its scores are recorded in the quality ledger alongside the existing behavioural metrics, so game quality becomes a tracked, comparable property of each run.
