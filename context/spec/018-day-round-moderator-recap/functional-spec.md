# Functional Specification: End-of-Round Day-Dynamics Recap

- **Roadmap Item:** Phase 6 → **Day-Round Moderator Recap → End-of-Round Day-Dynamics Nudge**
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

During the Day, players speak in rounds and may call votes to execute a suspect. Today, the only shared facts the Moderator surfaces are the moment-to-moment events: who died last night, who called a vote, how the tally landed. Nobody is ever handed a consolidated read of _where the game stands_ — how many of each side are left, how much of the Day's vote budget has been spent, whether anyone has gone to the gallows yet. Each player has to reconstruct that picture in their head from the running conversation.

This change gives every player a clear, common situational summary. **At the end of each Day round, the Moderator posts a brief public status to the whole table.** It names the day, the count of Law-abiding Citizens and Mafiosos still alive, how many votes have been called so far today, and who (if anyone) has been executed today and which side they turned out to be. The same status the human reads is read by the AI players, and it informs their later speech and votes the same way anything else said at the table does.

**This discloses nothing hidden.** Everything in the recap is already knowable from public play: night victims are always revealed as Law-abiding Citizens, executed players have their side revealed, and the game's starting composition is common knowledge (the human sets it at startup). A running "X Law-abiding and Y Mafiosos remain" count is therefore derivable by anyone paying attention — the recap just states it plainly in one place instead of making each player keep the tally themselves. No individual player's secret allegiance is ever revealed.

**Why it matters:** the AI-controlled town has never won a measured game at our largest table size. A standing weakness is town coordination — the Law-abiding players struggle to pool what they collectively know and act together. A clear, shared, repeatedly-refreshed picture of the standings is a candidate aid for that weakness: it gives every player the same factual footing to reason from on their next turn. Because the recap is a candidate aid whose effect we will want to measure, it can be **turned off** so a game plays exactly as it did before — letting a future study compare play with and without it side by side.

**Success looks like:** at the end of every Day round, a short, accurate status line appears in the day chat; its counts always match the true state of play; it never exposes a living player's secret side; it becomes part of the conversation the AI players reason over when they speak and vote next; and it can be cleanly switched off for comparison.

---

## 2. Functional Requirements (The "What")

- **The Moderator posts a status recap at the end of each Day round.**
  - A "round" is one full pass in which every surviving player has had their turn to speak.
  - The recap is posted publicly — every player at the table sees it, including the human.
  - **Acceptance Criteria:**
    - [x] Given a Day is in progress, when every surviving player has spoken once and the round completes, then the Moderator posts a short status recap in the day chat.
    - [x] Given the recap has been posted, when I look at the day chat, then it appears after that round's spoken messages and before the next round's discussion begins.

- **The recap states the current standings and the Day's vote activity.**
  - It includes: the current day number; how many Law-abiding Citizens are still alive; how many Mafiosos are still alive; how many votes to execute have been called so far today; and who, if anyone, has been executed today (with their revealed side).
  - **Acceptance Criteria:**
    - [x] Given it is the second day and 4 Law-abiding Citizens and 2 Mafiosos are alive, when the recap appears, then it names the day, states that 4 Law-abiding Citizens and 2 Mafiosos remain, and states how many votes have been called so far today.
    - [x] Given last night's kill reduced the living Law-abiding Citizens by one, when the first recap of the new Day appears, then its Law-abiding count is one lower than the prior Day's recaps.
    - [x] Given a vote was called earlier today and failed, when a later end-of-round recap appears, then its "votes called today" count includes that failed vote.

- **The recap reports whether anyone has been executed today.**
  - When no one has been executed yet today, the recap says so.
  - When a player was executed today, the recap names them and the side they were revealed to be.
  - **Acceptance Criteria:**
    - [x] Given no execution has happened yet today, when an end-of-round recap appears, then it states that no one has been executed today.
    - [x] Given a vote succeeded and a player was executed, when the Day's closing recap appears, then it names the executed player and states which side they were.

- **The recap fires at the end of every round, including the Day's final round.**
  - When the Day ends — whether by a successful execution, by exhausting the vote allowance, or by reaching the round limit — a closing recap reflecting the final standings is still posted, so the last round is never skipped and the execution outcome is captured.
  - **Acceptance Criteria:**
    - [x] Given a vote succeeds and ends the Day, when the Day closes, then a closing recap appears that reflects the post-execution standings (the executed player no longer counted among the living, and named as executed today).
    - [x] Given a Day ends because the round limit was reached with no execution, when the Day closes, then a final recap still appears and states that no one was executed today.

- **The recap appears every round, even when nothing has changed.**
  - On a quiet round where the counts are identical to the previous recap, the recap is still posted.
  - **Acceptance Criteria:**
    - [x] Given two consecutive rounds pass with no vote called and no one dying, when each round completes, then a recap appears at the end of both rounds, even though the numbers are the same.

- **The recap is written in the Moderator's voice and reveals nothing hidden.**
  - It reads as a brief, neutral Moderator status, consistent with the Moderator's other announcements.
  - It never reveals the secret role of any living player; it only restates facts already public or derivable from public play.
  - **Acceptance Criteria:**
    - [x] Given any recap during the game, when I read it, then it never states or hints at the secret side of any player who is still alive.
    - [x] Given a recap appears, when I read it alongside the Moderator's other lines, then it is brief and reads in the same neutral narrating voice.

- **The recap can be turned off (on by default).**
  - By default, the recap is active in every game.
  - A developer can switch it off (via an environment setting, consistent with the game's other configuration) so that games play exactly as they did before this feature — supporting a future side-by-side comparison (ablation) of play with and without the recap.
  - When switched off, no recap line appears at any point in the Day, and the rest of the game is unchanged.
  - **Acceptance Criteria:**
    - [x] Given the recap is left at its default, when a Day round completes, then a recap appears as specified above.
    - [x] Given the recap has been switched off, when a full Day plays out, then no recap line appears at any round end or at the Day's close, and every other part of the game behaves exactly as before.

---

## 3. Scope and Boundaries

### In-Scope

- A brief public Moderator status recap posted at the end of each Day round, including the Day's final round.
- Recap content: day number, living Law-abiding count, living Mafioso count, count of votes called so far today, and the executed-today result (none, or the named player and their revealed side).
- The recap being visible to all players and forming part of the shared conversation the AI players reason over for their subsequent speech and votes.
- Posting the recap every round, including rounds where nothing has changed since the last one.
- A switch (on by default) that turns the recap off, so a game runs exactly as it did before the feature — enabling a future ablation comparison.

### Out-of-Scope

- Any Night-phase status recap — this feature covers the Day phase only.
- Revealing any hidden information (a living player's secret side, private diaries, Mafia membership, vote-intent, or who pointed at whom during the Night).
- Changing any existing Day mechanics — speaking rotation, the three-vote allowance, the six-round limit, how votes are tallied, or the existing execution / "no one executed" announcements all stay exactly as they are.
- Running the ablation study itself or proving the town-coordination win-rate effect; this spec delivers the recap and the off-switch that makes the study possible, not the study or any measured outcome.
- All other roadmap items, which are automatically out-of-scope for this specification — including **AI Personas & Per-Game Memory** (per-AI private thoughts and diaries), **Asynchronous Day Chat**, the **End-of-Game Payoff** Moderator creative recap, and the Phase 7 **AI Tool-Use**, **Evidence Citation**, **Expanded Role Roster**, and **LLM-as-Judge** items.
