# Technical Specification: A Starter Table With Room For One Mistake

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev
- **Trigger:** [CR 007](../../change-requests/007-starter-lineup-balance-claim-and-default.md) (Accepted 2026-09-03)

---

## 1. High-Level Technical Approach

The production change is **two integers**. Everything else in this document exists because a test fixture is coupled to those integers, and because two harnesses record measurements whose denominators move with them.

That imbalance is the honest summary and it should shape how this is read: **§4 is the largest section, and correctly so.** A reviewer expecting the weight to sit in §2 will think something is missing.

### Verified: there is no production defect at eight players

Checked rather than assumed:

| gate | value | at 6+2 |
|---|---|---|
| `config.py` refusal `num_mafia >= num_citizens` | — | `2 < 6`, passes |
| `_MAX_TABLE_SIZE` | 12 | table of 8, **4 seats of headroom** |
| `_MAX_AI_NAMES` (`_MAX_TABLE_SIZE - 1`) | 11 | 7 AI names needed, passes |
| `_CONTEXT_WINDOW` default | 150 messages | not player-derived; `>= _MAX_TABLE_SIZE + 1` guard already covers 13 |
| `blunder_eval` anti-hang budget | `max_days * 60 + 40` | already Day-cap-derived by spec 023, reasoning explicitly about the largest table |

Everything that sizes by player count is **derived, not hard-coded** — the AI-name count, the role deck, speaking order, standings, the vote majority, the win check, and every roster render. The persona archetype pool refills and reshuffles on exhaustion, so seven AI players is fine. No fixed-height roster panel exists. **Spec 014's pre-registered concern about the eval budget is closed, not live**, and should be marked so rather than re-litigated during implementation.

Stale prose in `src/` with no behavioural effect: `config.py`'s "~40-45 messages at the default table" (now ~48-54 on Day 1, so the 150-message window spans ~2.8 days rather than "3+"), `eval_ledger.py`'s "seven people talking in turn", `blunder_eval.py`'s "today's 5 + 2" and its two CLI help strings saying "default 5" / "default 2".

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The default itself

`_DEFAULT_NUM_CITIZENS` moves from 5 to 6 in `src/graphia/config.py`; `_DEFAULT_NUM_MAFIA` stays 2. Nothing else in `src/` reads those constants. Both harnesses inherit the change automatically — `blunder_eval` writes the count env vars only when `--citizens` / `--mafia` are given, so an unset flag leaves the config default to win, and `ollama_smoke` calls `load_config()` bare.

Update the two CLI help strings and the three stale comments named in §1 in the same change, so no reader is told the old number by the tool that applies the new one.

### 2.2 The fixture coupling — the real work

**The defect.** `FakeSmall.invoke` raises when its one-shot queue drains. `_generate_names(count)` receives a scripted **6-name** roster, sees `len(names) != 7`, issues its corrective retry, and starves the queue; `_generate_names` catches only `ValidationError`, so the `AssertionError` escapes `generate_roster`. In UI-driven tests `src/graphia/ui/app.py:565` swallows it, so the failure arrives as a **5–16 second poll timeout with no mention of the cause.**

**The measurement that decides the fix.** `fake_small` has **85 call sites across 29 files**, and they do not want the same thing:

| population | sites | depends on the strict queue? |
|---|---|---|
| Scaffolding — result discarded, `call_count` never read | **62** | No |
| Multiplicity workaround — `outputs=[Roster, Roster]` / `_rosters(n)` | 20 | Only because the queue does not replay |
| **Contract** — scripts a failure and asserts `call_count` | **3** | **Yes** |

So the "called more times than scripted" guard is load-bearing at **three** sites and is an unwritten implicit assertion at the other 82 — where it is also **mute**, because the message never reaches the developer. That reframing matters: "29 files must be updated" makes the expensive fix look inevitable, and it is not.

**Decision: the fixture's two call forms mean two different things.**

- **`fake_small(names)` — the list form, 82 sites — becomes a stateless, count-derived fake.** No queue, no drain, no replay. Each `invoke` answers with exactly as many distinct names as the production code asked for, drawing the supplied list as a **pool** and extending it deterministically when the pool is short. `call_count` still increments, so any test wanting "generated exactly once" asserts it explicitly.
- **`fake_small(outputs=[...])` — 3 sites — keeps the strict one-shot queue that raises on drain.** That is where retry and coercion semantics are under test and where the guard earns its keep. **The `outputs=` form must never be routed to the permissive fake.** That constraint is what makes this whole approach safe, and it belongs in the fixture's docstring, not only here.

There is direct precedent in the same file: `_DynamicNightPointing` exists, documented "no queue, no replay, no exhaustion", because pre-scripting a value the production code derives at runtime is a race the test loses. `FakeSmall`'s list form is the same mistake one layer up.

**How the fake learns the count.** Extract the currently-inline `num_citizens + num_mafia - 1` from `generate_roster` into a **named helper** in `src/graphia/nodes/setup.py`, and have the fake call it. The alternative — the fake reading config and repeating the arithmetic — leaves two copies that can drift silently. This is a legitimate seam: it names a concept the codebase already reasons about in prose, in the two places that need it. **Do not** parse the count out of the rendered prompt; that couples the fake to prompt wording, which other specs change freely.

**Consequences.** The 62-site scaffolding sweep **evaporates** — those name lists never need editing, because they stop being lineup-sized rosters and become pools. `_rosters(n)` in the persona-bench test is deleted outright; its only reason to exist was the absent replay, and 19 call sites simplify with it. The next lineup change touches `config.py` and one tripwire test, and nothing else.

**Rejected: making the queue replay its last value.** Tempting and nearly free, and it must not be done — for a stronger reason than the guard it removes. At count 7 with a 6-name script, the retry returns the same six, coercion pads, and **the game proceeds with a seat named `Player-1`**. Every assertion that would notice is blind to it: the "all names appear" checks only look for names they already know, and **the byte-equal cross-mode dual-mode assertion passes, because both modes coerce identically.** Record that explicitly — otherwise someone will later cite "dual-mode smoke is green" as evidence this change was sound. It also converts a real regression class (the model's count is wrong and the retry never recovers) into something indistinguishable from a healthy run.

**Rejected: updating 85 literals to seven names.** Honest but self-defeating — it hard-codes the new default into 85 places and guarantees this exercise repeats on the next lineup change, without touching the fragility that caused it.

**Adopted narrowly, not as the primary fix: deriving name lists from config.** Right for the two or three tests that genuinely care *which* names appear (§4), wrong as the general answer — a module-level derived constant is computed at import time and would not see a per-test override, which is a live trap given two test modules vary the counts per test.

### 2.3 Bare table-size literals: derive, do not renumber

Roughly ten sites assert a table size that is a **config echo**, not the test's own construction — `== 7`, `== 6`, `!= 7` inside readiness predicates and post-setup assertions. Derive each from the resolved config rather than changing 7 to 8. Two of them (`!= 7` readiness predicates) currently fail at eight players by **timing out after five seconds with a message blaming the predicate rather than the lineup**; deriving them fixes the diagnostic as well as the number.

### 2.4 The default tripwire: exactly one owner

Three tests currently assert the default's value. That is two too many — it is precisely how a 5→6 sweep misses one and leaves a self-contradictory suite.

- **Sole owner:** `test_lineup_config.py`'s defaults test keeps its literal and its triple-equality idiom (`cfg.num_citizens == _DEFAULT_NUM_CITIZENS == 6`), which fails if *either* the constant or the intent moves. Its name encodes no number, so only the literal changes.
- **`test_default_lineup_unset_env_yields_seven` is doing two jobs**, and only one should own a literal. Strip the value assertions, derive everything from the resolved config (`expected_total`, `roles.count("mafia") == cfg.num_mafia`), and rename to something a number cannot invalidate. What it then tests is the invariant that actually matters: roster generation and role assignment agree with the config and with each other, at whatever the default is.
- **The lineup-recording test's `(5, 2)` is incidental** to its subject (that `None` overrides write neither env var). Compare against the imported default constants; it then never needs touching again.

### 2.5 The two harnesses that measure

- **`blunder_eval`** already stamps `settings.lineup` from the resolved config and its anti-hang budget is Day-cap-derived. **No change beyond the help strings.**
- **`persona_bench` records no lineup at all** — its settings block carries `diversity_enabled`, `collision_threshold`, `regen_attempts` and `temperature`, and its own comment cites the `lineup` precedent while not following it. Its personas-per-roster goes 6 → 7, so its similarity denominator moves from 15 pairs to 21, **with nothing in the record to explain the discontinuity.** Stamp the lineup, following `blunder_eval`'s shape. This is a provenance gap this change would otherwise widen silently.
- **`ollama_smoke`'s interrupt budget** is `max_rounds * 12 + 20`, sized per *round* rather than per *player*. At eight players each Day round costs more super-steps between human interrupts, so the "interrupt budget exhausted" return moves closer. Re-derive it rather than hope.

### 2.6 The first-run note is not code

The functional spec's criterion that the first measured run at the new table carries an explanatory note is satisfied by a **hand-authored string in that record's `notes` field** — the human-mutable field that already carries every prior finding. The harness deliberately knows nothing about the ledger's history, so "is this the first run at a new lineup?" is not a question it can answer without being given a memory it should not have. **No code, and therefore no test.**

Recorded because the alternative is a trap: any test of the record-writing path **must** redirect both the ledger path and the transcripts root at a temporary directory. That exact path once let ~25 synthetic records into the committed ledger through an early-bound signature default.

### 2.7 Documentation

- **`context/product/product-definition.md`** — two present-tense sentences describe the player being asked at launch for the role counts. That step does not exist and was explicitly ruled out when the counts were made configurable. Rewrite both to say the table is chosen before launching, and state what an unconfigured player gets.
- **`context/spec/014-configurable-role-counts/functional-spec.md`** — three statements read present-tense ("today's default five-plus-two", "the **current default** lineup"). The spec is Completed, so it is not rewritten; it gets a **superseded pointer** to CR 007 and this spec.
- **Left alone as historical**, per CR 007's own ruling: spec 001, spec 005 (note its claim is *stronger* — "regardless of the setting" — and will read as flatly wrong to a future reader, so it warrants the same pointer), spec 008, and every tutorial. The roadmap and CR 007 are already correct.

---

## 3. Impact and Risk Analysis

### System dependencies

Nothing architectural. The lineup resolves in config and is read at setup time; no graph parameter, no checkpoint shape, no provider interaction, no UI layout. Remote mode is unaffected — the counts travel as config to the runtime exactly as they do locally.

**No ADR 011 flag.** The counts are *already* the ablation mechanism: any prior lineup, including 5+2, is reachable by configuration, which is strictly better than a boolean flag. Recording the lineup on every measured run is what makes the change readable after the fact.

### Risks

| risk | mitigation |
|---|---|
| **A silently-coercing fixture fix seats a placeholder player and the suite cannot see it.** The strongest cross-mode assertion — byte-equal public logs — passes, because both modes coerce identically. | Rejected the replay-last fix for exactly this reason (§2.2). Recorded here so "dual-mode smoke is green" is never read as evidence about this change. Every "these names appear" assertion is additionally given a **cardinality** check (§4), because `assert name in rendered` over a known subset cannot detect an extra unknown member. |
| **The permissive fake could mask a real defect** in the retry/coercion logic. | Contained by construction: the three contract sites keep the strict queue, and the `outputs=` form is never routed to the permissive fake. That constraint is stated in the fixture docstring, not just here. |
| **Games get longer, and one test budget is a hard-coded iteration count sized against the old table.** At 6+2 the fakes never execute anyone, so parity arrives after **4** nights instead of 3, with the sum of alive counts across those days as the real cost — not "one more speaker per round", since dead players do not speak. | Replace the three `range(80)` loops with a shared driver keyed on a **wall-clock deadline** sized from the resolved player count, with a derived iteration cap as a secondary stop (§4). Named trade-off: a deadline is flakier on a loaded machine than an iteration count is reproducible — accepted because the current design *already* depends on wall-clock behaviour through its per-iteration pause, and mitigated by generous sizing plus the `slow` marker. |
| **`persona_bench`'s metrics shift with no lineup stamp**, unlike `blunder_eval`'s. | Stamp it (§2.5). Until then its records straddle the change invisibly. |
| A future lineup change repeats all of this. | The recommended fix leaves exactly two owners of a lineup number: the config constants and one tripwire test. |

### Adjacent defects found, cheap to fold in

Neither is caused by this change, but both live in the code the fixture fix must keep honest, and the existing count-parametrised sweep is one entry from covering them: **two consecutive validation failures** leave the roster unset and coercion produces an **all-placeholder** table, reachable in production against a flaky local model; and `_coerce_to_count`'s dedup is **case-insensitive**, so a model returning `Ivy` and `IVY` silently pads.

---

## 4. Testing Strategy

**The acceptance criteria come first, because they do not exist yet.** The functional spec's two win-margin criteria — the new table survives one mistaken execution, the old one does not — have **no test anywhere in the suite**, and they are the only assertions that pin what this change actually buys. Write them as pure tests over `check_win_condition`, which is read-only with no graph, no model and no RNG, **parametrised over `[(6,2), (5,2)]`** so one body proves the difference. The `(5,2)` arm doubles as the old-lineup criterion. This is the first slice, not an afterthought.

**The fixture change, and what proves it.** The permissive list form must be shown to answer with the count the production code asked for — at the default, and under a per-test override, since an import-time derivation would pass the first and fail the second. The strict `outputs=` form must be shown to still raise on drain. And the two forms must be shown to be **routed differently**, or the safety constraint in §2.2 is unenforced.

**Closing the vacuity this change introduces.** The retry-on-validation-failure test currently scripts a validation error then a six-name roster; at count 7 it keeps passing while silently exercising **coercion** instead of the retry-success path it documents — losing the suite's only coverage of a validation failure recovering. Split the claim: move the contract to a unit test on `_generate_names` with an explicit count, asserting the returned names are **exactly** the scripted ones (equality is what closes the hole — coercion cannot produce it) and that two calls happened; and leave the UI test as a recovery test, strengthened with an assertion that **no placeholder name appears** and that the roster line names exactly as many players as the config says.

**The generalisable rule, worth stating once:** `assert x in rendered` over a known subset cannot detect an extra unknown member. Every "these names appeared" assertion gains a cardinality check. That converts a family of silent-padding vacuities into loud failures, including the two that would otherwise hide a coerced placeholder.

**What must not be weakened**, listed so a sweep does not flatten it: the single default tripwire keeps its literal and its triple-equality form; the `_generate_names` / `_coerce_to_count` unit block keeps the strict queue and its call-count assertions, being the tests that would catch a real retry defect; the fail-fast validation tests are untouched and are what satisfy the criterion that an unworkable table is still refused up front; and the byte-equal cross-mode comparison stays **byte-equal** — made lineup-agnostic, never relaxed, with its blind spot recorded.

**Everything here is offline.** No live model, no network, no new marker beyond putting the slow UI drivers under the existing one. The measured-run criterion is satisfied by a hand-written note (§2.6), so it carries no test — and the reason is recorded, so its absence reads as a decision rather than an oversight.
