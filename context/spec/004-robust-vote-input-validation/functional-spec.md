# Functional Specification: Robust /vote Input Validation

- **Roadmap Item:** Polish / hardening — tightens the input-validation surface of the Day-phase `/vote` command introduced in Spec 001 §2.6, after two bugs were observed in real play.
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

During a Day discussion, the human player can call a vote to execute someone by typing `/vote <name>`. Spec 001 §2.6 defined what happens when that command is well-formed and targets a real alive player. Two real-play incidents have shown that **malformed or self-targeted `/vote` inputs sometimes end the game on the spot** — bypassing the entire vote ritual and any normal win-condition check.

This spec doesn't change the vote mechanic itself. It pins down the **exact, observable behaviour for every shape of `/vote` input** — including self-vote, nonexistent name, empty/bare command, and dead-target — so the player can never accidentally lose a game by mistyping. It also locks each case with end-to-end acceptance criteria so the bugs cannot recur unnoticed.

**Success looks like:** A human player who fat-fingers `/vote zzz` or `/vote ` (with no name) sees a single-line error in the chat panel and is asked to try again, with their turn not consumed. A player who explicitly types `/vote <their own name>` triggers a regular vote that all alive players poll on, and the game ends only if a normal win condition is met afterwards — never as a direct consequence of the self-vote command.

---

## 2. Functional Requirements (The "What")

### 2.1 Valid `/vote <alive-player>` — baseline (already specified in 001 §2.6, restated here for completeness)

- **As a** human player on my Day turn, **I want to** type `/vote Alice` to call a vote against an alive player named Alice, **so that** the vote ritual begins and everyone polls Yes/No.
  - **Acceptance Criteria:**
    - [x] `/vote Alice` (where Alice is alive and not me) starts the vote-to-execute flow exactly as Spec 001 §2.6 prescribes.

### 2.2 `/vote` against self — allowed, both outcomes proceed normally

- **As a** human player on my Day turn, **I want to** be able to call a vote against myself (e.g. for roleplay or to test the mechanic), **so that** the command behaves consistently and never special-cases self-targeting.
  - **Acceptance Criteria:**
    - [x] `/vote <my-own-name>` (case-insensitive, fuzzy match per existing rules) initiates a regular vote against me as the target.
    - [x] All alive players, **including me**, are polled Yes/No on executing me — same poll order, same prompts, same tally rules as any other vote.
    - [x] **Vote-fails branch (I survive):** If the vote fails (no strict majority), the Moderator announces "The vote fails.", I am NOT executed, the failed vote counts against the three-vote-per-Day cap, and the speaking round resumes — identical to a failed vote against anyone else.
    - [x] **Vote-passes branch (I am executed):** If the vote passes, I am executed, my role is revealed, the Day ends, and the win-condition check runs ONCE — exactly as it would for any execution. The game ends only if that single win check returns a winner. There is no extra game-end trigger caused by the self-target.
    - [x] **Both branches are exercised by separate end-to-end test scenarios** with deterministic outcomes (seeded AI ballots or fixture-injected vote results). Neither branch is taken on faith — both pass/fail self-vote paths are pinned by tests.
    - [x] In particular: `/vote <my-own-name>` MUST NOT skip the poll, MUST NOT execute me unconditionally, and MUST NOT end the game without a win condition being met.

### 2.3 `/vote <nonexistent-name>` — re-prompt, turn not consumed

- **As a** human player who fat-fingers a player name, **I want to** see a clear error and a chance to retry, **so that** I don't lose my turn or the game to a typo.
  - **Acceptance Criteria:**
    - [x] `/vote zzz` (where no alive player matches `zzz`) shows the error "No such player. Try again." (or equivalent single-line message) in the chat panel.
    - [x] My turn is NOT consumed — I am re-prompted to either retry the vote, speak instead, or do nothing.
    - [x] The Day does NOT advance (no new speaking turn for anyone else, no Day-end, no vote counted against the three-vote cap).
    - [x] The game does NOT end.

### 2.4 Empty / bare `/vote` — distinct "Usage" error

- **As a** human player who hits Enter on `/vote` alone, or `/vote ` followed by whitespace, **I want to** see a specific usage hint, **so that** I learn the correct command shape rather than being told a name was wrong (when in fact I gave no name at all).
  - **Acceptance Criteria:**
    - [x] Bare `/vote` (no second token), `/vote ` (whitespace only), and `/vote\t\t` all show the error "Usage: /vote <name>" (or equivalent single-line usage hint) and re-prompt me — turn not consumed, Day not advanced, game not ended.
    - [x] This error is distinct from the §2.3 "No such player. Try again." message — the player can tell whether they typed an unknown name or omitted the name entirely.

### 2.5 `/vote <dead-player>` — re-prompt

- **As a** human player who forgets that Bob was killed last Night, **I want to** see the same "no such player" message when I try to vote against him, **so that** dead players aren't a special case I need to think about.
  - **Acceptance Criteria:**
    - [x] `/vote Bob` where Bob is in the roster but `is_alive == False` triggers the same "No such player. Try again." re-prompt as a nonexistent name.
    - [x] Turn not consumed, Day not advanced, game not ended.

### 2.6 The slash command is strictly `/vote` followed by whitespace or end-of-line

- **As a** human player typing freely in chat, **I want to** be able to use the word "voted" or "/votefor" in normal speech without accidentally triggering a vote, **so that** chat doesn't unpredictably switch into vote mode.
  - **Acceptance Criteria:**
    - [x] `/voted yesterday` is treated as a normal spoken line, not as `/vote d yesterday`.
    - [x] `/votefor Alice` is treated as a normal spoken line, not as `/vote for Alice`.
    - [x] Only `/vote` followed by whitespace (or nothing) parses as the slash command.

---

## 3. Scope and Boundaries

### In-Scope

- All shapes of `/vote` input the **human** player can type during their Day turn: valid alive target, self, nonexistent name, dead name, bare command, whitespace-only argument, and `/vote`-prefix-without-space.
- The error message and re-prompt loop for invalid inputs.
- End-to-end behaviour: turn consumption, Day advancement, vote count, win-check timing.
- Explicit test coverage for both self-vote outcomes (executed and not-executed).

### Out-of-Scope

- AI players' vote-initiation path (`_ai_day_action` returning `kind == "vote"`). The AI side already constrains itself via the `DayAction` Pydantic schema and the `Pointing` / `Ballot` schemas — different code path, different validation story, not implicated in either reported bug.
- Changes to how Yes/No ballots are polled or tallied. Once a vote starts via a valid `/vote`, Spec 001 §2.6 rules apply unchanged.
- Changes to the three-vote-per-Day cap, the six-round safety cap, or the random tie-break rules.
- Changes to the AI's ability to vote against itself or other players (the AI's logic is its own concern).
- All remaining roadmap items: Long-Term Cross-Game Memory & Career Stats, AI Provider Flexibility, Setup Flexibility, Richer Night Resolution, AI Personas & Per-Game Memory, Asynchronous Day Chat, End-of-Game Payoff, AI Tool-Use Demonstration, Expanded Role Roster.
