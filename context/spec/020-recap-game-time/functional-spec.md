# Functional Specification: Game-Time in the Recap

- **Roadmap Item:** Phase 6 → Recap-Driven Day Decisiveness → **Game-Time in the Recap**
- **Status:** Completed
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

The day-round recap tells every player *where* the game stands, but nothing about *how far the Day has progressed* or *how little time is left to act*. Without that sense of time, the AI town tends to dawdle — in a recent measured review, 7 of 10 games ran on without ever reaching a decision.

This change gives the Day a felt sense of time by adding an **in-world clock** to the recap. Each Day round maps to a time of day, advancing from morning toward midnight, so the recap reads like the day burning down toward Night. As the clock climbs toward midnight, every player — human and AI — feels the pressure to act before the Day ends and Night falls.

The clock is purely a reading of the round the Day is already on; it does not change how the Day works. The mapping, across the up-to-six rounds of a Day:

| Round | In-world time |
| ----- | ------------- |
| 1 | 9 AM |
| 2 | 12 PM (noon) |
| 3 | 3 PM |
| 4 | 6 PM |
| 5 | 9 PM |
| 6 | 12 AM (midnight — Night falls) |

A Day that ends early — because a vote executes someone, or the day's votes are used up — simply stops earlier on the clock; it never jumps ahead to midnight.

**Success looks like:** every recap shows, beside the day number, the in-world time for the round it covers; the time advances one step per round, reaching midnight only on a Day that runs its full course; a Day that ends early shows the time of the round it ended on; and the marker reads naturally in the Moderator's voice while changing nothing else about the recap.

---

## 2. Functional Requirements (The "What")

- **The recap shows an in-world time of day for the current round, beside the day number.**
  - Each round's recap carries the time-of-day for that round, drawn from the mapping above (9 AM at round 1, advancing to midnight at round 6).
  - **Acceptance Criteria:**
    - [x] Given the first round of a Day completes, when its recap appears, then it shows the day number and the time **9 AM**.
    - [x] Given consecutive rounds pass within a Day, when each round's recap appears, then the time advances one step per round in the order 9 AM → 12 PM → 3 PM → 6 PM → 9 PM → 12 AM.
    - [x] Given a Day runs to its sixth round, when that round's recap appears, then it shows **12 AM (midnight)** — the latest time the clock reaches.
    - [x] Given a Day ends early at, say, round 3 (a vote executes someone), when the Day's closing recap appears, then the clock shows **3 PM** and does not jump ahead to midnight.

- **The clock reads naturally and changes nothing else.**
  - The time marker is a brief, natural part of the Moderator's recap line; the rest of the recap is untouched.
  - **Acceptance Criteria:**
    - [x] Given any recap, when it is read alongside the standings, then the time marker is brief and fits the Moderator's neutral narrating voice.
    - [x] Given the clock is present, when the rest of the recap is read, then the standings (living counts by side), the votes-called-today count, and the executed-today line are all unchanged.

---

## 3. Scope and Boundaries

### In-Scope

- An in-world clock shown in the day-round recap, beside the day number, mapping each Day round to a time of day (9 AM at round 1, advancing in steps to midnight at round 6).
- A Day that ends early showing the time of the round it ended on, never jumping to midnight.

### Out-of-Scope

- **Changing any Day mechanics.** The clock is display-only: it does not change how many rounds a Day has (still up to six), the three-vote allowance, or anything about how the Day plays out — it only *reads* the round the Day is already on.
- Feeding the recap (clock included) into the AI players' reasoning — that is the sibling spec, **Recap-Aware AI Reasoning** (019).
- Any real-world / wall-clock time — the clock is purely an in-world flavor of the Day's progress.
- Night-phase timing or any clock outside the Day recap.
- All other roadmap items, which are automatically out-of-scope for this specification.
