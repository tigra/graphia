# Technical Specification: AI Behavioral Integrity & Outcome Tracking

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Two coordinated changes, executed **measure-first** (the spec's §2 order and the slicing both enforce it):

1. **Measurement first** — extend the blunder-eval harness, the ledger record, and the viewer with two new per-run blocks: **`outcomes`** (win-rate by side) and **`vote_activity`** (vote-initiation counts by side and by game-day). Land this, then **capture a baseline on the unchanged behaviour** (a clean-tree `make blunder-eval` per provider, committed) so the fixes have a real before-picture.
2. **Behaviour fixes second** — the n=20 baseline's three pathologies all trace to **role-blind prompts**: `DAY_SPEAK_USER_TEMPLATE` / `AI_VOTE_USER_TEMPLATE` are filled only with names, never the actor's role/team/win-condition, and the lone teammate whisper ages out of the 30-message context window. Fix by **injecting role + win-condition + teammate list (and an explicit ballot relationship flag) directly into the prompts**, plus a **Day-passivity nudge**. All fixes are **prompt-level** — there is **no mechanical guard**: self-execution and teammate-execution are both *discouraged by persuasion*, not forbidden, because each can be a rare legitimate strategy (self-sacrifice; bussing). Then re-run and compare in the ledger.

The interface between the two halves is narrow and already verified in code: per finished game, `state["winner"] ∈ {"law_abiding","mafia","draw",None}`; per-day vote initiations are reconstructable from the **message log** (day-open markers + `VOTE_INITIATE_ANNOUNCE_TEMPLATE`) plus the initiator's side from final `players` — the same channel `score_vote_blunders` already walks. **No new state channel, no graph-topology change, no schema change** (`DayAction`/`Ballot` untouched — Bedrock's flat-schema constraint is not re-engaged).

This is the **first gameplay-changing increment** since the Day-phase integrity trio — it edits the real game prompts. Acceptance for the behaviour fixes is **measured against the baseline** (CI-separated rate drops), consistent with architecture §6's non-determinism posture — there is no deterministic guard to unit-test; the prompt nudges are verified live, and only the new *measurement* code is deterministically tested.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Win-rate by side — the `outcomes` block — **[Agent: python-backend]**

- Add `winner: str | None` to `_GameCapture`, set in `_play_one_game` from `state.get("winner")` (beside the existing `players=`/`messages=` reads). Failed-early games never produce a capture, so they're naturally excluded (already counted in `quality.games_failed_early`).
- New pure helper `tally_outcomes(winners) -> dict` (the `_facets` idiom; unit-testable on a synthetic list). Buckets each completed game into `law_abiding` / `mafia` win, `draw`, or `no_winner` (`winner is None` — typically the eval round cap, since the scripted human always votes No). Record block (top-level):

  ```yaml
  outcomes:
    games: 20
    law_abiding: {wins: 11, rate: 0.55, ci_low: …, ci_high: …}
    mafia:       {wins: 6,  rate: 0.30, ci_low: …, ci_high: …}
    draw: 2            # plain count — not a side, no rate
    no_winner: 1       # winner=None (round cap / unresolved)
    note: '<fixed passive-scripted-human caveat>'
  ```
- **Denominator = `games` (completed)**, so the four buckets partition the run: `la.wins + mafia.wins + draw + no_winner == games` (a README-stated invariant a reader sanity-checks). **Wilson CI** on the two side win-rates only (reuse `wilson_ci(wins, games)` — derived/supplementary, no version bump); `draw`/`no_winner` stay bare counts. `games == 0` → emit the block with zero counts, omit rates/CI (no `ZeroDivisionError`).
- The **passive-human caveat** lives in two places: a fixed machine-emitted `outcomes.note` constant (`_OUTCOMES_HUMAN_CAVEAT`, beside `_BEDROCK_UPDATE_NOTE` — immutable, distinct from the human-mutable top-level `notes`) **and** a README legend subsection.

### 2.2 Vote-initiation activity by side × day — the `vote_activity` block — **[Agent: python-backend]**

- New pure scorer `score_vote_activity(messages, players) -> dict`, mirroring `score_vote_blunders`'s message-log walk (same `SystemMessage` filter, `_name_index`, AI-only via `_is_ai`, template-derived anchors). Walk once tracking a `current_day` counter incremented on each **day-open marker**; for each `VOTE_INITIATE_ANNOUNCE` resolve the initiator → `role` and increment `counts[(side, day)]`. Record block (top-level):

  ```yaml
  vote_activity:
    by_side: {law_abiding: 4, mafia: 0}   # ALWAYS both keys, zero included
    by_day:  {day_1: 2, day_2: 1, day_3: 1}
  ```
- **⚠ Load-bearing constraint #1 — explicit-zero is the deliberate inverse of `metrics`' absent-omission.** `by_side` emits **both** side keys with integer counts (zero included) by literal construction, so a run where the AI never initiates a vote renders `by_side: {law_abiding: 0, mafia: 0}` / `by_day: {}` — a **committed, visible zero**, never an omitted block. This is the whole point: the Nova-silent-Day pathology must read as `0`, surviving into the viewer cell. (`metrics` *omits* a no-opportunity rate because a 0.0 there misleads; `vote_activity` *emits* the zero because the absence of activity is itself the signal.) State this divergence in the scorer docstring and the README. `by_day` is naturally sparse — empty `{}` when no initiations; do **not** pre-seed `day_N: 0` (day count varies per game). Day keys sorted by integer suffix (`day_10` after `day_2`).
- **⚠ Load-bearing constraint #2 — the day-open prefix trap.** `DAY_OPEN_NO_VICTIM_TEMPLATE` (`"Day breaks."`) is a strict prefix of `DAY_OPEN_VICTIM_REVEAL_TEMPLATE` (`"Day breaks. {name} was…"`). Build both anchors via `_template_to_regex` (full-anchored `^…$`), test the victim regex first, use **exact-equality** for the no-victim case — so each day boundary increments the counter exactly once.
- Batch totals summed across the run's completed games; `by_side` and `by_day` are independent marginals of the same grand total (`sum(by_side.values()) == sum(by_day.values())`).

### 2.3 Key order, versioning, viewer surfacing — **[Agent: python-backend]**

- **Fixed key order:** `run → code → provider → settings → quality → outcomes → vote_activity → metrics → notes` (the two new game-dynamics blocks sit after `quality`, before `metrics`; `notes` stays last).
- **`METRICS_VERSION` does NOT bump.** Per the `ci_low`/`ci_high` precedent: the new blocks are orthogonal new measurements outside the versioned `metrics` map, not a change to the blunder-family detection rules — bumping would falsely flag every prior blunder rate as incomparable. Old records simply lack the blocks (the viewer's `_dig` absorbs it, like pre-provenance records lacking `code`). README "Versioning" gains one bullet. (Forward hook: a future change to *how* a winner/initiation is computed would warrant its own `outcomes_version`/`vote_activity_version` — not added now, YAGNI.)
- **`eval_ledger.py` / viewer:** two compact fixed columns appended to `_FIXED_COLUMNS` (head, before the metric block so the `len(columns) − len(METRIC_ORDER)` right-justify split is preserved): **`Wins (LA/M)`** (`LA .55 / M .30`, absent→blank) and **`Votes (LA/M)`** (`LA 4 / M 0`; **present-with-zero renders `LA 0 / M 0`, absent renders blank** — the explicit-zero guarantee carried into the viewport). Two new `render_detail` sections (`outcomes`, `vote_activity`) slotted between `quality` and `metrics`, absent→`—`. Add a single `winner` scoped field to `SEARCH_FIELDS` + the free-text blob (derived keyword: the side that won the majority of completed games, else `draw`/`mixed`). No `METRIC_ORDER` change.

### 2.4 The before-picture baseline — workflow, no flag — **[Agent: python-backend]**

Ordering note for `tasks.md` (no harness flag — `code.commit` + `code.dirty` + `--note` already distinguish pre/post-fix runs): **(A)** land §2.1–2.3 + tests, commit; **(B)** on the clean tree, `make blunder-eval ARGS="--provider ollama --games N --note 'spec-013 baseline: pre-fix'"` and again for bedrock, commit the records as the baseline (clean tree ⇒ `code.dirty: false`, attributable); **(C)** the behaviour fixes (§2.5–2.7) land on later commits; **(D)** re-run per provider with `--note 'spec-013 after fix'`, diff against the baseline in `make view-ledger`. This is how "one spec" still honours measure-first.

### 2.5 Role / team / win-condition injection — the core fix — **[Agent: langgraph-agentic]**

- New node-side pure helpers in `nodes/day.py`: `_win_condition_line(role)` (wording **lifted verbatim from `check_win_condition`** so the prompt objective matches the mechanical rule — load-bearing) and `_teammates_str(actor, players)` (alive mafia names excluding self).
- **`DAY_SPEAK_USER_TEMPLATE`** gains `{role_label}`, `{win_condition}`, `{team_line}` (mafioso → "Your fellow Mafiosi (keep this secret): …"; citizen → ""), with a "never reveal your secret role/teammates" instruction. **`AI_VOTE_USER_TEMPLATE`** gains those plus `{relationship}` — the node-computed flag that moves behaviour most, both phrasings a *nudge* (not an imperative), symmetric: `voter.id==target.id` → "**{target} is YOU. Executing yourself normally loses you the game — vote No unless this is a deliberate self-sacrifice play.**"; mafia→mafia → "**{target} is your fellow Mafioso. Executing a teammate normally costs you the game — vote No unless you have a deliberate bus-the-teammate reason.**"; else "". `AI_VOTE_SYSTEM`'s dangling "consider your own role" is tightened now that the role is actually supplied.
- Node computes the fields for **all** actors (incl. under the test fakes) and applies them to the retry message block too. Inject **directly**, never via the scrolling `{context}` whisper.
- **⚠ Knowledge-boundary invariant (load-bearing — do not "symmetrize"):** the grounding discloses only what an actor's role legitimately knows. `{team_line}` is **Mafia-only** (`_teammates_str` enumerates *mafia* peers; for a Law-abiding Citizen it is `""` — there is **no** law-abiding teammate list, ever). The ballot `{relationship}` flag emits only "is YOU" or "is your fellow Mafioso" — **never** "is a fellow Citizen" or any other-player allegiance. So a Law-abiding voter learns nothing about any other player's side; a Mafioso learns only its own team (which it already knew from the night intro). A future change must not add a law-abiding equivalent for "symmetry" — that would collapse the deduction game (townsfolk would identify Mafia by elimination). The template-field smoke test asserts a citizen's rendered prompt contains no teammate/other-allegiance disclosure.

### 2.6 Self-execution & teammate-execution: prompt nudges, no mechanical guard — **[Agent: langgraph-agentic]**

Both are handled **entirely by the §2.5 prompt injection** — the `{relationship}` flag ("{target} is YOU …" / "… your fellow Mafioso …") plus the role/team/win-condition grounding — with **no deterministic guard in `collect_votes`**. Per the user decision (2026-06-15): each can be a *rare legitimate strategy* — a self-sacrifice when already doomed or to build town credibility; a "bussing" play to deflect suspicion from the Mafia — so the goal is a **sharp, CI-separated reduction** (self-approval from ~0.63, teammate-approval from ~0.86, teammate-initiation from ~0.50), **not** a hard zero. A mechanical ban would forbid these tactics outright and over-constrain the model; persuasion preserves the tail. Effect is **measured** (the existing `self_vote.*`/`peer_vote.*` rates with Wilson CIs, before vs after), not unit-tested — there is nothing deterministic here.
- **`self_vote.initiation` stays structurally prevented** by the existing `_accept` check (`target_id != speaker.id`) — unchanged; *starting* a vote against yourself is a degenerate opening move (baseline already 0/13), distinct from choosing not to fight a vote already called on you. (If a future spec wants self-nomination as a self-sacrifice opening, that's an `_accept` relaxation — out of scope here.)
- **The human path is untouched** — `collect_votes` gains no AI-branch guard, so the human still answers their own/any ballot via the interrupt exactly as the spec-004 self-vote tests pin.

### 2.7 Day-passivity nudge — **[Agent: langgraph-agentic]**

Reword `DAY_SPEAK_SYSTEM`'s "Prefer speaking unless you have a concrete suspicion" (which Nova reads as "always speak") to: convert a *genuine, specific* suspicion into a vote, speak to gather info when there's no lead, **don't vote every turn or accuse without a reason** — framed for both sides (Citizens convict, Mafia misdirect). A prompt nudge whose effect is measured by §2.2's `vote_activity` (zero → non-zero initiations on both providers).

---

## 3. Impact and Risk Analysis

- **Blast radius:** game side — `prompts.py` (4 templates) + `nodes/day.py` (2 helpers, field computation, 1 guard); measurement side — `blunder_eval.py`, `eval_ledger.py`, viewer (no structural change), `evals/README.md`; tests. **No graph topology / state channel / schema change.**
- **Risk — new template fields break the mocked suite if a `.format` kwarg is missing** → a `KeyError` on *every* AI turn under the fakes. *Mitigation:* node formats every new field for all actors; a `test_slice6_day.py` + `test_slice7_vote.py` run catches it immediately. No existing test asserts the `DAY_SPEAK_*`/`AI_VOTE_*` *input* prompt text or field-set (verified — they assert *output* templates + state); the Night `MAFIA_POINT` roster parse is untouched.
- **Risk — no mechanical floor on self/teammate execution.** With both as nudges (per the user decision), a weak model could still self- or teammate-execute more than hoped. *Mitigation:* accepted by design (the tail is legitimate strategy); the §2.4 before/after measurement quantifies the reduction, and if a model proves intractable the relationship-flag wording can be sharpened in a follow-up A/B — no code change needed. `collect_votes` is untouched, so no vote-tally or human-path regression risk at all.
- **Risk — measuring a fix that didn't help (or regressed win-rate).** *Mitigation:* the entire §2.1–2.4 measure-first ordering exists for this — the baseline makes any change provable or falsifiable, and CIs guard against small-sample over-reading (the spec-011 "rigor reverses the pilot" lesson).
- **Win-rate is under a passive scripted human** — a consistent comparable measure, not true balance (caveat baked into `outcomes.note` + README). Recorded, not hidden.
- **Determinism posture unchanged (architecture §6):** behaviour fixes are non-deterministic prompt content measured by the make-gated harness; no mechanical guard is added, so the only deterministic code here is the new *measurement* (tested) and the unchanged `_accept` self-initiation rejection (regression-pinned). The mocked suite reaches no real model.

---

## 4. Testing Strategy — **[Agent: testing]**

- **Measurement (pure, offline, synthetic inputs from the real templates):** `tally_outcomes` — four buckets, partition invariant, Wilson CI on sides, `games==0` no-crash, `None`-only → `no_winner==games`. `score_vote_activity` — by_side/by_day counts; **the explicit-zero headline test** (day-open markers, zero initiations → `by_side {0,0}` present, `by_day {}`); the day-open **prefix-trap** test (mixed victim/no-victim → counter increments once each); human-initiator excluded; multi-game fold; marginal-sums-equal. `render_record` — both blocks in fixed key order, fixed caveat present, the literal `by_day: {}` rendered (not omitted), and a `yaml.safe_load` round-trip (the viewer parses it). `eval_ledger` flatten — populated vs **pre-013 record** (cells blank, sections `—`, no raise) and the **`Votes` zero-vs-absent distinction** (`LA 0 / M 0` vs blank). Viewer Pilot — new columns/sections appear on a new record, `—`/no-crash on an old one.
- **Behaviour fixes — measured, not pytest-asserted.** With self/teammate execution and Day-passivity all now **prompt nudges** (no mechanical guard), there is **nothing deterministic to unit-test** on the behaviour axis — the self/peer/passivity improvements are verified by the §2.4 before/after `make blunder-eval` runs (CI-separated drops; non-zero `vote_activity` on both providers), which are the spec's real acceptance. The only deterministic checks are: (a) a **`self_vote.initiation` regression pin** — `_accept` still rejects a self-targeted vote initiation (likely already covered in `test_vote_validation.py`/`test_slice7_vote.py`); (b) a **template-field + knowledge-boundary test** — confirm the new `DAY_SPEAK_*`/`AI_VOTE_*` fields (incl. the `{relationship}` flag, empty and non-empty) format cleanly under the fakes (a missing `.format` kwarg would `KeyError` every AI turn), **and assert the boundary deterministically**: a **Mafioso's** rendered prompt names its fellow Mafiosi; a **Law-abiding Citizen's** rendered prompt contains **no** teammate list and **no** other-player allegiance (its `{team_line}` is empty and its `{relationship}` is only ever "is YOU"/""), and a law-abiding voter's ballot prompt never labels the target's side. This pins the invariant in code, not just prose; (c) the **spec-004 human self-vote tests stay green unchanged** (the human path is untouched — no AI-branch guard added).
- All offline tests run in `uv run pytest -q`; `safe_llm` untouched (no new LLM call site — the guard removes one on the self path).
