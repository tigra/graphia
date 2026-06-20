# Functional Specification: Fuller Multi-Day Discussion Window for AI Players

- **Roadmap Item:** Day-prompt-quality / Day-decisiveness follow-up — the third lever on what an AI player reasons from, after *Recap-Driven Day Decisiveness* (spec 019, standings into the prompt) and *Role-Specific Day Guidance* (spec 024). Relates to the **Town-coordination / Day-decisiveness** and **Repetition** backlog threads. Not a distinct roadmap phase item.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

When an AI player takes its Day turn, it is shown the **recent discussion** so it can reason about who said what. Today that window is small — only the **last ~30 messages** of visible play. A single active Day at the default table runs ~40–45 messages (several rounds of speeches, the per-round recaps, and any vote sequences), so **the window doesn't even cover one full Day**: by the end of a Day, a player has already lost that Day's opening exchanges, and it can never see anything from previous days. Earlier accusations, who suspected whom, and how a player voted two days ago are simply gone from its view.

For a deduction game this is a real handicap. Suspicion is cumulative — "she defended him yesterday and is defending him again" is exactly the kind of cross-day continuity the town needs to coordinate, and the kind of consistency a Mafioso must maintain to keep its cover. With too short a memory, every player effectively resets each Day. It also plausibly worsens **repetition** (a player can't see, and so re-treads, points made a dozen turns ago).

This change gives each AI player a **fuller window of recent discussion — spanning at least the last three days of events** — so its speech and votes are grounded in the running story of the game, not just the last few turns. The standing situational *summary* is already injected separately (spec 019, "N Law-abiding vs M Mafia remain…"); this complements it by carrying the actual recent **discussion** behind those numbers, across days.

The window must be **sized to the gameplay model's real working memory, with headroom** — generous enough to hold 3+ days, but not so large that the model is diluted by stale, low-signal history (a deliberately bounded window, not the entire game). Crucially, a fuller window only helps if the model **actually receives it**: the local-model path must be confirmed to read a context this large, or the oldest content — which includes the player's own role, objective, and instructions — would be silently dropped, doing more harm than good.

**Success looks like:** in a game several days long, an AI player's reasoning context reaches back across multiple days of discussion rather than a fraction of the current Day; the player's own role and instructions are never lost no matter how long the game; and a measured run records **whether** the fuller memory improves play (cross-day coherence, repetition, decisiveness) without harming it. Under the effort-not-results principle ([CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)), confirmed **or** refuted is a complete result.

---

## 2. Functional Requirements (The "What")

- **AI players reason from a fuller, multi-day window of recent discussion.**
  - The recent-discussion context shown to an AI player at its Day turn spans **at least roughly the last three days of events** (a large multiple of today's window), so events from prior days — earlier accusations, suspicions voiced, votes cast — inform its current speech and vote, not just the current round.
  - **Acceptance Criteria:**
    - [ ] Given a game that has reached its third day or later, when an AI player takes a Day action, then its recent-discussion context includes meaningful events from at least the previous ~3 days (not only the current Day or round).
    - [ ] Given a player who was accused or who voted on an earlier day, when a later-day turn is taken, then that earlier moment is within the window the player reasons from.

- **The window is sized to the gameplay model's real working memory, and never drops the essentials.**
  - The window is set generously against the model's actual context capacity (the local model path supports about a 32K-token working context; the cloud model far more), with comfortable headroom — large enough for 3+ days yet bounded well short of filling the context, so the model is not diluted by an overlong history.
  - No matter how long the game runs, the player's **own role, objective, persona, the situational summary, and its instructions are always present** — they are never crowded out or dropped to make room for history.
  - **Acceptance Criteria:**
    - [ ] Given the longest realistic game and the fullest window, when an AI player's prompt is prepared, then its role, objective, and instructions are still present in full — on both the local and the cloud provider.
    - [ ] Given the chosen window, when its size is reviewed, then it occupies only a comfortable fraction of the model's working context (headroom remains), rather than filling it to the limit.

- **The model must actually receive the fuller window (no silent dropping).**
  - The benefit depends on the model genuinely reading the larger history. The local-model path's **effective working context must be confirmed to match the size this window assumes**; if it cannot, the window is reduced to what the model truly reads, so content is never silently discarded mid-prompt.
  - **Acceptance Criteria:**
    - [ ] Given the local (Ollama) provider, when a fuller window is sent, then the model's effective working context is verified to be large enough to read all of it — with none of the oldest content (the player's role/instructions) silently dropped.
    - [ ] Given a model whose effective context cannot hold the intended window, when the prompt is prepared, then the window is trimmed to fit rather than overflowing.

- **The window size is an adjustable setting (so the change is ablatable).**
  - The window length is a configurable value; it defaults to the new, fuller size and can be set back to the prior small size to reproduce the old behavior for a side-by-side comparison (per the ablation-flag convention, [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md)).
  - **Acceptance Criteria:**
    - [ ] Given the setting at its default, when AI players take Day actions, then they reason from the fuller multi-day window.
    - [ ] Given the setting returned to the prior small value, when AI players take Day actions, then they reason from the old short window (for A/B).

- **The effect is measured, not assumed (effort-not-results).**
  - Whether fuller multi-day memory actually improves play is an open question this change lets us test, not a promise. A measured comparison against the recorded baseline is run and recorded, confirmed or refuted.
  - **Acceptance Criteria:**
    - [ ] Given a measured run after this change, when its outcomes are compared with the recorded baseline — repetition, win-rate by side, votes initiated, share resolved vs `no_winner` — then the comparison is recorded and the hypothesis (does fuller cross-day context improve coherence/decisiveness without harming play?) is logged confirmed or refuted, either being a complete result.

---

## 3. Scope and Boundaries

### In-Scope

- Enlarging the recent-discussion window that feeds an AI player's Day **speaking-turn and vote** prompts so it spans roughly the last three or more days of events.
- Sizing that window to the gameplay model's real working context with comfortable headroom, so the player's own role/objective/instructions are never dropped and the prompt isn't bloated into dilution.
- Confirming the local model actually reads a context this large (so nothing is silently truncated), and trimming the window to fit if it does not.
- Making the window length an adjustable, default-on setting so the prior behavior is reproducible for ablation.
- Measuring the effect against the recorded baseline, under effort-not-results.

### Out-of-Scope

- The **standing situational summary** (spec 019) — already injected into the prompt and unchanged here; this spec concerns the surrounding *discussion* window, not that summary.
- **Night pointing / the Mafia Night prompt** — this widens the window only where it is used today (the Day speaking turn and the vote).
- The **human** player's view — the human already sees the full scrollback; this is about what the AI players reason from.
- **Summarizing or compressing** older history (a different approach to long-context memory) — this spec simply widens the verbatim window; compression is not in scope.
- **Per-AI private thoughts / diaries** (Phase 6 roadmap items) — a separate per-AI memory channel.
- Changing which messages are **visible** to a player (the private-whisper filtering is unchanged) or raising the maximum table size.
- All other roadmap items, which are automatically out-of-scope for this specification.
