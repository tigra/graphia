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

_With a working skeleton in place, add the configurability and richer consensus mechanics the product definition calls for._

- [ ] **Setup Flexibility**
  - [ ] **Configurable Role Counts:** Replace the fixed lineup with startup prompts asking the human for the number of Law-abiding Citizens and Mafiosos, then randomly assign roles so every run starts fresh.

- [ ] **Richer Night Resolution**
  - [ ] **Multi-Round Mafia Consensus by Pointing:** Have Mafiosos converge on a victim across multiple rounds of private pointing, falling back to the Phase 1 single-round majority-with-random-tie-break only if they fail to agree within a round cap.

---

### Phase 3

_Once the game mechanics are solid, layer in the features that make Graphia feel alive and showcase the advanced LangGraph patterns the project is really about._

- [ ] **AI Personas & Memory**
  - [ ] **AI Character Sheet Generation:** At game start, have a creative LLM produce a distinct personality, backstory, and voice for each AI player, persisted for the whole game so their behavior feels consistent.
  - [ ] **Per-AI Private Diaries:** Before each Night, have every surviving AI player write a short private diary entry capturing their suspicions and plans, kept hidden during play and surfaced at the end.

- [ ] **Asynchronous Day Chat**
  - [ ] **Rate-Limited Concurrent AI Chatter:** Replace the synchronous Day loop with an asynchronous one in which AI players post messages over time, subject to a per-player rate limit, so discussions feel like a real room rather than a round-robin.
  - [ ] **Concurrent Human Typing with Live Display:** Render AI messages token-by-token in the terminal while the human can type their own message at the same time, without lines colliding or corrupting each other.
  - [ ] **Vote-Opens-Lock-Chat Handoff:** When any player calls a vote mid-conversation, cleanly freeze the chat mid-stream and transition all players (human and AI) into voting mode until the ballot resolves.

- [ ] **End-of-Game Payoff**
  - [ ] **Moderator Creative Recap:** When a win condition triggers, feed the Moderator the dead players' diaries, the day-chat logs, and the night-kill vote logs, and have them deliver a short creative story that reveals hidden twists the human couldn't see during play.

---

### Phase 4

_Features planned for future consideration — explicitly deferred from v1 per the product definition. Priority and scope may be refined based on what we learn from earlier phases._

- [ ] **Expanded Role Roster**
  - [ ] **Detective Role:** Introduce a Detective who can privately investigate one player per Night to learn their true alignment.
  - [ ] **Protector Role:** Introduce a Protector (e.g., Doctor/Bodyguard) who can privately shield one player per Night from the Mafia kill.
  - [ ] **Role-Mix Configuration:** Let the human configure which extended roles are in play and in what numbers at startup, maintaining game balance.