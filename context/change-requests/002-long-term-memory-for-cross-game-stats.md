# Change Request: Long-Term AgentCore Memory In; AI Tool-Use Demoted to Further Improvements

- **CR ID:** 002
- **Date:** 2026-05-06 (amended same day)
- **Author:** Alexey Tigarev
- **Status:** Proposed

---

## 1. Triggered By

The change was prompted by a modification to (mark all that apply):

- [x] `context/product/product-definition.md` — section: §1.4 Success Metrics, §2.1 Core Features, §2.2 User Journey, §3.1 In-Scope, §3.2 Out-of-Scope (and pending §2.1 / §3.1 follow-up to descope AI tool-use)
- [x] `context/product/roadmap.md` — phase / item: roadmap restructured to reflect both this CR and CR 001 — Phase 2 Hosted AgentCore Deployment (v1.1 hard scope), Phase 3 Long-Term Cross-Game Memory (v1.2 hard scope), Phase 7 Further Improvement Possibilities (AI Tool-Use Demonstration demoted here)
- [ ] `context/spec/[NNN-slug]/functional-spec.md` — section: [name]
- [x] Other: extends CR 001 in two ways — (a) introduces a long-term cross-session AgentCore Memory use-pattern distinct from CR 001's per-game diary pattern, and (b) revises CR 001's v1.1 placement of AI tool-use (AI-player tools and Moderator helper tools) by demoting it to Phase 7

**Context (1–2 sentences):** Two coupled scope changes are captured in this CR: (a) the product definition added a long-term, cross-session use of AgentCore Memory for cross-game career and aggregate stats (initiations and votes for night kills and day executions, role-broken-down counts, game outcomes) surfaced via a pre-game greeting and post-game career-stats panel, overriding the previous §3.2 prohibition on cross-session persistence; and (b) AI tool-use (originally CR 001 v1.1 hard scope) was demoted to a Phase 7 further-improvement item under Graphia's design-driven-by-realistic-needs principle. Both changes were made the same day in the same workflow run; the two are amended into one CR rather than logged sequentially. The roadmap restructure (Phase 2 = AgentCore deployment, Phase 3 = long-term Memory, Phase 7 = tool-use) reflects both.

---

## 2. Summary of Change

Two coupled scope changes:

**(a) Long-term cross-game Memory added (v1.2 hard scope).** Graphia adds cross-game career and aggregate statistics as a core capability, demonstrating long-term cross-session AgentCore Memory in remote mode. The game tracks night-kill initiations and votes, day-execution initiations and votes, game outcomes, and human-player career stats by role. These are surfaced to the human at two points: a one-paragraph career-summary greeting on launch (before the role-count prompts) and a career-stats panel appended to the Moderator's end-of-game recap. In remote mode the long-term store is AgentCore Memory; in local mode the same data is persisted to a file in the game's local data directory so dev work without AWS still sees stats accumulate. Full game transcripts, diaries, and vote-by-vote replays remain non-persistent across sessions — only the stats summaries needed for these views are stored.

**(b) AI tool-use demoted from v1.x hard scope to Phase 7 further improvements.** AI-player tools (Day-phase investigation, evidence-builder) and Moderator helper tools (kill-log summary, diary fetch, recap-input assembly), originally introduced in CR 001 as v1.1 hard scope, are demoted to a Phase 7 *further improvement possibility* — explicitly deferred from v1, genuinely aspirational. The Moderator's end-of-game recap remains a v1.x scope item but is implemented via direct state reads, not explicit tool calls. The AgentCore Gateway in Phase 2 still fronts the per-game diary read/write surface (so the Gateway demonstration is preserved), but the rich tool-use surface waits for Phase 7.

---

## 3. Driver (Why This Change?)

This CR captures **two** scope decisions made the same day in the same workflow run; both drivers are recorded below.

**Primary driver (pick all that apply):**

- [x] **User / stakeholder feedback** — drove change (a), the long-term Memory addition
- [ ] **Implementation learnings** — something discovered while building that invalidated an earlier assumption
- [ ] **New external constraint** — regulatory, vendor change, deprecation, cost, deadline
- [x] **Strategic pivot** — drove change (b), the AI tool-use demote (refinement of what the reference project should demonstrate)
- [ ] **Error correction** — the earlier decision was wrong on its own terms
- [ ] **Scope adjustment** — descope or rescope based on capacity / priority
- [ ] **Other:** [describe]

**What was the previously-agreed assumption?** Two assumptions are overridden:

1. **(For change (a))** Each Graphia game is a fresh, fully isolated session — no persistent state of any kind crosses session boundaries, and AgentCore Memory namespaces are scoped to a single game's lifetime — explicitly recorded as out-of-scope in product-definition §3.2.
2. **(For change (b))** Per CR 001, AI tool-use (Day-phase investigation, evidence-builder, Moderator helpers) was a v1.1 hard-scope demonstration — bundled with the AgentCore deployment scope as a stakeholder ask.

**What changed about those assumptions?**

1. **(For change (a))** A specific, narrowly-defined cross-session persistence layer is now in scope: end-of-game stats summaries (event counts, role-broken-down aggregates, game outcomes, human-player career data). Full game transcripts, diaries, and vote-by-vote replays remain non-persistent. Long-term AgentCore Memory becomes the explicit demonstration target of this scope addition in remote mode, with a parallel local file for local mode.
2. **(For change (b))** AI tool-use is demoted from v1.x hard scope to Phase 7 *further improvement possibility*. Both AI-player tools and Moderator helper tools demote together. The end-of-game Moderator recap remains a v1.x scope item but is implemented via direct state reads.

**Detailed reasoning — change (a), long-term Memory addition:**

- **Stakeholder:** the project stakeholder — same stakeholder as CR 001, follow-up ask in a subsequent exchange.
- **Stakeholder ask (verbatim shape):** also have something for use of *long-term* AgentCore Memory, e.g. by keeping statistics on initiation and votes of night kills and initiation and votes during the day, for the human player and overall, broken down by role (obedient / mafia, etc.) — knowing how many games were played, how many kills and attempts there were, etc.
- **Motivation:** consistent with CR 001 — primary motivation is hands-on demonstrable skill in this AgentCore pattern (the *long-term cross-session* Memory use-pattern, specifically distinct from the per-game diary use-pattern that CR 001 already covered). Secondary motivation (inferred): showcase the breadth of AgentCore Memory use-patterns within one reference, so the project credibly stands in for both common patterns engagements are likely to encounter.
- **Local-mode authoring decision:** the project stakeholder's ask did not specify local-mode behaviour. The author elected to mirror the long-term store in local mode via a file in the local data directory (rather than skip stats in local mode entirely) so dev work without AWS still sees stats accumulate. This means local mode is no longer fully stateless across sessions — a deliberate softening of the previous local-mode posture, accepted because the cross-game store is small (counters and outcome summaries) and orthogonal to game mechanics.
- **Scope discipline preserved:** the cross-session store covers stats summaries only — full game transcripts, diaries, and vote-by-vote replays remain explicitly non-persistent. This keeps the cross-session footprint tight and avoids drifting toward "save/load of past games" which is still out of scope.

**Detailed reasoning — change (b), AI tool-use demote:**

- **Decision-maker:** the author, applying Graphia's *design-driven-by-realistic-needs* principle while restructuring the roadmap.
- **Reasoning (verbatim shape):** "the cases genuinely fitting the game design are degenerate for tool-use, basically mostly solvable by structured output, and it doesn't make sense to use a feature just for the sake of using it; we have to demonstrate the design really driven by realistic user needs, and thus instead of tool-use, we will demonstrate long-term memory."
- **Underlying principle:** Graphia is a reference project, but its demonstration value depends on the design feeling realistic, not contrived. The Mafia-game use-cases originally proposed for tool-use (investigation lookup of prior statements, evidence-builder over logs) are well-served by *structured output* (Pydantic-shaped LLM responses) rather than full agentic tool calls — the latter would be overkill for the actual problem shape. Demonstrating tool-use here would amount to "using a feature for its own sake", which the reference project explicitly rejects.
- **What replaces tool-use as the next demonstration target:** long-term cross-session AgentCore Memory (this CR's change (a)). Cross-game career stats are a *real* player-facing feature with genuine value — they pass the realistic-needs test, where tool-use does not.
- **What's preserved at v1.x despite the demote:** the AgentCore deployment story remains intact — Runtime + Gateway + Memory + Observability. The Gateway in Phase 2 still fronts the per-game diary read/write surface (a small but genuine tool surface, still meaningful as a Gateway demonstration). The Moderator's end-of-game recap stays in scope but reads state directly. Phase 7 holds the rich tool-use demonstration for if/when realistic needs emerge.
- **Why amending CR 002 rather than creating CR 003:** the demote was made the same day in the same `/awos:roadmap` workflow run as the Memory addition; the two changes are tightly coupled (long-term Memory replaces tool-use as the next-demonstrated capability) and arose from the same process of refining what v1.x should actually showcase. Logging as a fresh CR 003 would obscure that they're two halves of the same refinement.

**Could this have been anticipated earlier?**

- *Change (a):* Partially. CR 001 already brought AgentCore Memory into scope, so a second Memory use-pattern was conceivable. What was not anticipated: a specific stakeholder ask for long-term cross-session statistics arriving as a follow-up the day after CR 001, and the consequent need to override the §3.2 item that ruled out cross-session persistence outright.
- *Change (b):* In hindsight, yes. CR 001 admitted AI tool-use to v1.1 by riding on the project stakeholder's broad ask without applying the realistic-needs filter. Applying that filter at CR 001 time would have shown the Mafia use-cases were a poor fit for tool-use. The demote is essentially a deferred application of the principle.

---

## 4. Nature of Change

- [ ] **Additive** — adds new behaviour without altering old (rare reason for a CR; usually a fresh spec covers this instead)
- [x] **Revisionary** — overrides or contradicts a previously-agreed requirement
- [ ] **Removal / descope** — withdraws a previously-agreed requirement

The §3.2 prohibition on cross-session persistence and on AgentCore Memory namespaces extending beyond a single game's lifetime is overridden — narrowly, for stats summaries only.

---

## 5. Impact on Existing Requirements

| Affected artifact (path + section)                                          | What changes                                                                                                                                                                                                                           | Already implemented? |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `context/product/product-definition.md §1.4`                                | New success metric: cross-game stats accuracy and visibility across pre-game greeting and post-game panel; the "advertised concepts" list is widened to call out long-term cross-session AgentCore Memory as a distinct locatable item; the "AI tool calls visibly influence Day-phase decisions" metric (added by CR 001) needs to be **withdrawn** as a follow-up to this amendment, since tool-use moves to Phase 7 | Partially (Memory additions applied; tool-use metric removal pending) |
| `context/product/product-definition.md §2.1`                                | New "Cross-game career and aggregate statistics" core feature; AgentCore deployment bullet widened to mention Memory's dual role (per-game + long-term); the "AI tool use during Day phase" and "Moderator tools" core features (added by CR 001) need to be **removed** from §2.1 as a follow-up, with the recap-feature wording adjusted to describe direct state reads instead of tool calls | Partially (Memory additions applied; tool-use removal pending) |
| `context/product/product-definition.md §2.2`                                | User journey now includes a pre-game career-summary greeting and a post-game career-stats panel; the user-journey passage describing AI tool-call beats during the Day phase needs to be **removed** as a follow-up | Partially (Memory additions applied; tool-use passage removal pending) |
| `context/product/product-definition.md §3.1`                                | Adds: cross-game stats capability, pre-/post-game stats display, long-term AgentCore Memory as the remote-mode store, local-file stats persistence for local mode; updates the Local-mode bullet to include the local stats file; the in-scope items covering AI-player tool calls and Moderator helper tool calls (added by CR 001) need to be **removed** as a follow-up | Partially (Memory additions applied; tool-use items removal pending) |
| `context/product/product-definition.md §3.2`                                | The "no cross-session persistence" item is replaced by a tighter wording (only stats summaries persist; full transcripts/diaries/replays remain non-persistent; in-progress games still non-saveable); a new explicit out-of-scope for a dedicated `graphia stats` standalone command; a new explicit out-of-scope to be added as follow-up: "AI tool-use during the Day phase and Moderator helper tools — deferred to Phase 7 further-improvement possibilities; the v1.x Moderator recap reads state directly" | Partially (Memory additions applied; new tool-use out-of-scope item pending) |
| `context/product/product-definition.md` — version                           | 1.1 → 1.2 (already bumped); a further bump to 1.3 may follow the §2.1/§3.1 tool-use cleanup, at the author's discretion                                                                                                                | Yes (1.2 applied; further bump optional) |
| `context/product/roadmap.md`                                                | Restructured this turn: Phase 2 = Hosted AgentCore Deployment (v1.1 hard scope, replacing the old optional Phase 2 framing); Phase 3 = Long-Term Cross-Game Memory (v1.2 hard scope, new); Phase 4 = AI Provider Flexibility (renumbered); Phase 5 = Setup Flexibility & Richer Night Resolution (renumbered); Phase 6 = Game Feel: Personas, Per-Game Diaries, Async Day Chat, Endgame Payoff (renumbered, with Per-AI Diaries description widened to mention AgentCore Memory in remote mode); Phase 7 = Further Improvement Possibilities (expanded to hold AI Tool-Use Demonstration alongside the existing Expanded Role Roster) | Yes (this update) |
| `context/product/architecture.md`                                           | Needs to record the dual AgentCore Memory use-patterns (per-game diary store from CR 001; long-term cross-game stats store from this CR) and the local stats file as the local-mode parallel; reflects that local mode is no longer fully stateless across sessions; should also record that AI tool-use is *not* a v1.x architectural concern (deferred to Phase 7) so architectural decisions don't pre-empt it | Yes (exists; predates this CR) |
| `context/change-requests/001-agentcore-and-tools-in-scope.md`               | CR 001's tool-use scope is **revised** by this amendment — AI-player tools and Moderator helper tools demote to Phase 7. CR 001 is not rewritten; this CR records the revision. CR 001's AgentCore deployment + per-game Memory scope **stand** unchanged | Yes (CR 001 logged; tool-use scope revised here) |

**Rework / migration required (any "Yes" or "Partially" above):**

- **Product-definition tool-use cleanup (pending follow-up):** §1.4 has a tool-use success metric; §2.1 has tool-use core features (AI Day-phase tools, Moderator tools); §2.2 has tool-call beats in the user journey; §3.1 has tool-use in-scope items. All four were added by CR 001 and now need to be removed/relocated to align with the demote. The Moderator recap feature in §2.1 needs its wording softened from "uses tools" to "reads state directly". A new §3.2 out-of-scope item should record the tool-use deferral.
- **Local-mode posture softening (already applied):** the product-definition local-mode promise no longer reads "fully stateless across sessions" — local mode now keeps a small cross-game stats file in a local data directory. Reflected in §3.1.
- **Architecture doc revisit:** describe two distinct AgentCore Memory use-patterns (per-game diaries vs. long-term cross-game stats) and their local-mode parallels, rather than a single Memory use-pattern; explicitly note tool-use is out of v1.x architecture scope. Belongs in `/awos:architecture` follow-up.
- **Spec scope:** new acceptance criteria are needed for the cross-game stats capability (accurate accumulation, correct pre-/post-game display, parity between local-file and AgentCore Memory backings). These belong in a fresh spec produced via `/awos:spec`, not bolted onto spec 001. No fresh spec is produced for the tool-use Phase 7 item — it is genuinely deferred.

---

## 6. Decision

- **Decision:** _TBD — Status: Proposed_
- **Decided by:** _[pending]_
- **Decided on:** _[pending]_
- **Rationale:** _[pending — leave for reviewer/future-self after spec/tasks fall out]_

---

## 7. Follow-up Actions

- [x] Run `/awos:roadmap` to restructure the roadmap (Phase 2 AgentCore deployment, Phase 3 long-term Memory, Phase 7 tool-use demote). *Done this turn.*
- [ ] Run `/awos:product` (Update Mode, §2.1, §2.2, §3.1, §3.2 and possibly §1.4) to clean up the residual tool-use scope language: remove the AI Day-phase tools and Moderator-tools core features, soften the recap feature to read state directly, drop the tool-use user-journey passage, remove the tool-use in-scope items, and add a new §3.2 item recording that tool-use is deferred to Phase 7. Optional version bump to 1.3 with this cleanup.
- [ ] Run `/awos:architecture` to capture the two AgentCore Memory use-patterns (per-game diary store and long-term cross-game stats store), the local-mode stats file as the parallel cross-game store, the resulting nuance that local mode is no longer fully stateless across sessions, and the explicit decision that AI tool-use is out of v1.x architectural scope (deferred to Phase 7).
- [ ] Run `/awos:spec` to draft a fresh spec for the cross-game stats capability — events tracked, aggregation shape, pre-/post-game display, and parity between AgentCore Memory and local-file backings. Engineering-level decomposition (data shapes, namespace keys, file format, refactors) belongs in that spec's technical-considerations and tasks files — not in this CR.
- [ ] Do **not** run `/awos:spec` for the Phase 7 tool-use items — they are genuinely deferred; specs land if and when the realistic-needs filter ever passes them in a future revisit.
- [ ] Treat spec 001 as untouched: its acceptance criteria stand for local mode and no items are unmarked.
