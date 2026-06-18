# Functional Specification: AI Character Personas

- **Roadmap Item:** Phase 6 — **AI Personas & Per-Game Memory** → **AI Character Sheet Generation**. (The sibling Phase 6 items — Per-AI Day-Round Private Thoughts, Per-AI Private Diaries, Asynchronous Day Chat, End-of-Game Payoff — are separate specs.)
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today every AI player is mechanically identical — a blank, interchangeable voice. They point, speak, and vote competently, but the table feels like a roomful of identical agents rather than a cast of characters. This increment gives each AI player a **persona** — a distinct personality, a short backstory, and a characteristic manner of speaking, created fresh at the start of every game — so the Day chat reads like real, recognizable people arguing, and the game "feels alive."

A persona is more than flavor for the Mafia. A Mafioso lives a double life: it has a **true self** — it knows it is Mafia — and a **public legend**, a cover identity it performs at the table to blend in with the Citizens. A Law-abiding player has nothing to hide, so its persona is simply who it is. During the game the personas are never shown as profile cards; they are **felt** through how each character talks and carries itself. Only when the game ends are the personas revealed — including the satisfying turn that the affable neighbour everyone trusted was, in truth, a Mafioso playing a part.

**Desired outcome:** every game opens with a fresh, distinct cast of AI characters; each speaks and behaves consistently in-character throughout; Mafiosos convincingly perform a cover without betraying themselves; and at game end the human learns who everyone really was.

**Success is measured by:** across a game, each AI player's voice and temperament stay recognizably consistent; no two AI players feel like the same character; a persona never lets the human tell Mafia from Citizen by character alone; the game's rules and turn structure are entirely unaffected by personas; and at the end the human is shown each AI's true character, with each Mafioso's public legend contrasted against its true self.

---

## 2. Functional Requirements (The "What")

### 2.1 Every AI player gets a fresh, distinct, persistent persona

- **As a** player, **I want** each AI opponent to be a distinct character, **so that** the game feels like a room of real people rather than identical bots.
  - **Acceptance Criteria:**
    - [x] Given a new game starts, when the AI players are set up, then each AI player is given a persona — a distinct **personality**, a short **backstory**, and a characteristic **manner of speaking**.
    - [x] The **human player is not given a persona** — personas are for AI players only.
    - [x] **No two AI players** in the same game share the same persona; each is recognizably distinct.
    - [x] A persona is created **fresh for each game** (a new game produces a new cast) and stays **fixed for the whole game** — an AI's character does not drift or change between rounds.
    - [x] During play, **no persona is shown** to the human as a profile or description — it is conveyed only through how the character speaks and behaves.

### 2.2 A persona shapes how a character behaves, never the rules

- **As a** player, **I want** each AI to act in character, **so that** personalities are felt in the conversation — without changing how the game plays.
  - **Acceptance Criteria:**
    - [x] Given the Day discussion is underway, when an AI player speaks, then its words reflect its persona's personality and manner (e.g., a bold character argues forcefully; a cautious one is measured and hedging), and this tenor stays **consistent for that character across the whole game**.
    - [x] A persona **may colour a character's inclinations** — how readily it suspects, accuses, or defends, and how much it says — but it **never overrides the player's actual role goal** (a Mafioso still plays to win for the Mafia; a Citizen still tries to find the Mafia).
    - [x] A persona **never changes the rules or the turn structure**: every AI still takes its turn, speaks when it is its turn, and casts every vote and makes every Night choice the game requires of it. A "reserved" or "quiet" character is reserved **in tone only** — it does not skip turns, abstain, or forfeit any action.

### 2.3 A Mafioso has a true self and a public legend

- **As a** player, **I want** Mafiosos to convincingly pose as ordinary townsfolk, **so that** catching them is a real challenge and unmasking them is a payoff.
  - **Acceptance Criteria:**
    - [x] Given an AI player is a Mafioso, when its persona is created, then it has **two layers**: a **true backstory** (consistent with being a Mafioso, which that player is aware of) and a separate **public legend** — a cover personality and manner it presents to the rest of the table.
    - [x] During play, an AI Mafioso **speaks and behaves according to its public legend**, and never reveals — through its persona, voice, or backstory — that it is a Mafioso or that its public self is a cover.
    - [x] Given an AI player is Law-abiding, when its persona is created, then it has a **single, honest persona** (no cover) — what it presents is who it is.
    - [x] A persona — legend or honest — **never hints at a player's secret allegiance**: an attentive human cannot tell Mafia from Citizen by persona alone, only by their play.

### 2.4 Personas are revealed when the game ends

- **As a** player, **I want** to learn who everyone really was after the game, **so that** the deception pays off and the cast is satisfying in hindsight.
  - **Acceptance Criteria:**
    - [x] Given the game has ended (a side has won, or the game closed), when the result is shown, then **each AI player's true persona is revealed** to the human — its character, backstory, and manner — covering **all** AI players, whether they survived or were eliminated.
    - [x] For an AI player who was a Mafioso, the reveal makes the **deception visible**: the public legend it performed during the game, contrasted with its true self as a Mafioso.
    - [x] The reveal is shown **after** the game's outcome and **does not appear at any earlier point** in the game.

---

## 3. Scope and Boundaries

### In-Scope

- **Generating a distinct persona for each AI player at game start** (personality, backstory, manner of speaking), fresh per game and persistent across the game.
- The **two-layer Mafioso persona**: a true (Mafioso-aware) backstory plus a public legend cover; a Law-abiding player gets a single honest persona.
- Personas **expressed only through in-character speech and behaviour** during play (no profile shown), colouring voice and temperament but **never the rules or turn structure**, and **never leaking allegiance**.
- A **plain reveal** of every AI player's true persona at the end of the game, contrasting a Mafioso's legend with its true self.

### Out-of-Scope

- A **persona for the human player** — the human is themselves.
- **Per-AI Day-Round Private Thoughts** and **Per-AI Private Diaries** (separate Phase 6 specs) — though they will draw on the personas this spec creates.
- The rich **End-of-Game Moderator creative recap / storytelling** (separate Phase 6 item — *End-of-Game Payoff*): this spec reveals personas **plainly**; weaving them into a narrative with hidden twists is that item's job.
- Any change to the game's **rules, turn order, win conditions, or vote/pointing mechanics** — personas are an expressive layer only; temperament flavours style and inclination *within* the unchanged rules, never a mechanical advantage or a skipped action.
- **Showing or hinting at personas** (or the Mafioso cover) **during play**.
- **All other roadmap items** — Asynchronous Day Chat (Phase 6); and Phase 7's AI Tool-Use, Expanded Roles, Human Evidence Citation, and LLM-as-Judge Evaluation.
