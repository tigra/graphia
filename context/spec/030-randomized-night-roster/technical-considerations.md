<!--
This document describes HOW to build the feature at an architectural level.
It is NOT a copy-paste implementation guide.
-->

# Technical Specification: Randomized Night-Pointing Roster Order

- **Functional Specification:** [030 Randomized Night-Pointing Roster Order](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

The Mafia's Night candidate list — the living Law-abiding players, today rendered in `players`-dict insertion order — is shuffled into a fresh random order before it is shown to the pointer. The change touches exactly one phase node module (`src/graphia/nodes/night.py`) plus the two cross-cutting threading surfaces every ADR-011 flag passes through (`src/graphia/config.py` and `src/graphia/graph.py` / `src/graphia/runtime/graph_builder.py`).

The shuffle reuses Graphia's established mechanical-RNG seam (architecture §6 "Determinism Posture & Testing Conventions"): a single module-level helper over the **module-global `random`** RNG, mirroring `graphia.nodes.day._shuffle_order`, `graphia.nodes.setup._shuffle_deck`, and `graphia.nodes.night._shuffle_mafia_order`. This makes the order vary run-to-run, reproducible under a fixed in-test seed, and pinnable by a single monkeypatch point in tests — no `GRAPHIA_SEED`, no `config.seed`.

It is gated behind a new **default-on** ablation flag (per [ADR 011, "Default-on feature flags make gameplay-influencing changes ablatable"](../../adr/011-ablatable-gameplay-feature-flags.md)), so the prior fixed order is reproducible for A/B. The candidate **set** is unchanged — only its order differs; eligibility, the multi-round agreement mechanic ([spec 015](../015-*/), the `mafia_round_start` → `mafia_point` loop), kill resolution, and win conditions are all untouched.

The load-bearing risk this doc analyzes in §3 is **RNG consumption**: when ON, the shuffle draws from the module-global RNG, shifting every downstream RNG-dependent decision in a seeded run. The design must guarantee (a) OFF takes **no** RNG draw (exact prior seeded behavior), and (b) the dual-mode byte-equal smoke (`tests/test_dual_mode_smoke.py`) stays green because the seam is mode-independent and consumes RNG identically in both modes under the same seed.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The candidate-list seam (where the order is actually produced)

`src/graphia/nodes/night.py`. The single most important structural finding: the candidate list is **not** precomputed once per round. It is recomputed fresh on every pointer super-step inside `mafia_point` via `_alive_law_abiding(state)` (which returns living Law-abiding players in `players`-dict insertion order), and that list flows to **both** pointing paths:

- **AI path:** `mafia_point` → `_ai_pick_target(alive_law_abiding=…)` → `_roster_lines(alive_law_abiding)` renders the `"{name}: {id}"` lines into `MAFIA_POINT_USER_TEMPLATE`.
- **Human path:** `mafia_point` builds the `"point"` interrupt payload's `options` list directly from the same `alive_law_abiding`.

Both paths today see the same insertion-ordered list. The fix introduces a new module-level seam that produces a (possibly) reordered copy of that list, applied at the **single point of assembly** so both paths shuffle identically:

- **New helper `_shuffle_night_roster(candidates: list[PlayerState], *, enabled: bool) -> list[PlayerState]`** (name provisional; see §5).
  - Responsibility: return the candidate list reordered. When `enabled`, return a fresh shuffled copy via `random.shuffle` over the module-global RNG (mirror `_shuffle_mafia_order` / `_shuffle_order` exactly — copy the list, shuffle the copy, return it; never mutate the input). When **not** `enabled`, return the input order **with no RNG draw whatsoever** (see §3.1 — this is the load-bearing OFF contract).
  - The candidate **set** is invariant: it returns the same elements, only the order changes. This is the unit-testable contract for functional AC "contains exactly those players — none missing, none extra."
  - It reads only `PlayerState` identity; it must never read `role`/`is_human` (same fairness property `test_slice_day_order_fairness.py` locks for `_shuffle_order`).

The `enabled` flag is threaded into `mafia_point` (see §2.3) and applied once at the top of the candidate-list derivation, so the single reordered list feeds both the AI roster render and the human `options` payload. This keeps AI and human pointers seeing a **consistent** order within a super-step.

### 2.2 Replay-safety placement (the interrupt interaction)

`mafia_point` contains the human-pointer `interrupt()` and therefore **re-executes its whole body on resume** (the project's interrupt replay rule — interrupts replay the node). Any RNG draw in a replayed node is a correctness hazard: a naive in-`mafia_point` shuffle would re-draw on every human resume, consuming extra global-RNG state per replay and (worse) potentially producing a different order than the one the human was shown.

The existing Night design already solved the structurally-identical problem for the per-round Mafioso order: the non-deterministic `_shuffle_mafia_order` runs in `mafia_round_start` — a node with **no** `interrupt()`, its own committed super-step — and `mafia_point` only ever *reads* the committed `night_mafia_order` (see the `mafia_round_start` / `mafia_point` docstrings, spec 015 §3 replay-safety). This spec must honor the same discipline. Two viable placements, to be settled in §5:

- **Option A — compute the shuffled candidate order once per round in `mafia_round_start`, store it in state, read it in `mafia_point`.** Add a `night_law_order: list[str]` channel (a list of candidate ids, replace reducer) populated alongside `night_mafia_order` in `mafia_round_start`; `mafia_point` resolves that id-order back to `PlayerState`s for both paths. This is the strict mirror of the existing replay-safe pattern: the only RNG draw lives in an interrupt-free super-step and is never recomputed on a human resume. **Cost:** a new state channel; the candidate set can change within a round only on death, which does not happen mid-round, so a per-round freeze is semantically safe.
- **Option B — shuffle inside `mafia_point` but only before the `interrupt()`, accepting the day-precedent shape.** `_shuffle_order` is called inline at its node (`day_turn` round-wrap) which itself has no interrupt at the call site. But `mafia_point` *does* interrupt, so an inline draw here would sit in replayed code. This is **rejected** unless the draw can be proven to land in committed state before the interrupt and never re-run — which in practice collapses back into Option A.

**Recommendation: Option A.** It is the only placement that satisfies the existing §3 replay-safety invariant without a fragile "draw-before-interrupt" argument, and it reuses the exact `mafia_round_start` super-step that already hosts the round's other shuffle. The functional spec's "randomized order **each Night**" is satisfied per-round (a strictly finer grain than per-Night), and per-round reshuffling matches how `_shuffle_order` reshuffles each Day round.

### 2.3 The flag: config field + dual-builder threading

New ADR-011 default-on flag, mirroring the `scripted_player_active` / `role_guidance_enabled` precedents exactly:

- **`src/graphia/config.py`:**
  - New field on `GraphiaConfig` (frozen dataclass): `night_roster_shuffle_enabled: bool = True` (defaulted so tests constructing the config directly stay valid).
  - In `load_config()`: `night_roster_shuffle_enabled = _env_flag("GRAPHIA_NIGHT_ROSTER_SHUFFLE", default=True)` (env-var name provisional; see §5). `_env_flag` already gives default-on-with-explicit-falsy semantics — no new parsing.
  - Add to the `GraphiaConfig(...)` constructor call at the end of `load_config()`.
- **`src/graphia/graph.py` `_assemble_graph(...)`:**
  - New keyword parameter `night_roster_shuffle_enabled: bool = True`.
  - `mafia_point` is currently registered as a bare node (`builder.add_node("mafia_point", mafia_point)`). Wrap it in a `partial` binding the flag — `partial(mafia_point, night_roster_shuffle_enabled=night_roster_shuffle_enabled)` — exactly as `night_open` binds `max_days`. (If Option A is chosen, the flag is instead bound into `mafia_round_start`, where the draw lives; `mafia_point` stays bare and reads the frozen `night_law_order` from state. **This is the cleaner wiring and another point in favor of Option A** — the flag rides the same node as the draw it gates.)
  - `build_graph(...)` passes `night_roster_shuffle_enabled=config.night_roster_shuffle_enabled` into `_assemble_graph`.
- **`src/graphia/runtime/graph_builder.py` `build_runtime_graph(...)`:**
  - New keyword parameter `night_roster_shuffle_enabled: bool = True`, forwarded into `_assemble_graph`, mirroring how `max_days` / `context_window` are forwarded. The production Runtime entrypoint passes `load_config().night_roster_shuffle_enabled`.
  - The threading anti-drift test (the spec-024 precedent asserts `build_runtime_graph` carries/forwards the flag) extends to this flag — both modes must build the graph with the same gate so local and remote can't diverge.

### 2.4 Behavior summary table

| Flag | Behavior | RNG draw? |
| --- | --- | --- |
| `GRAPHIA_NIGHT_ROSTER_SHUFFLE` unset / truthy (default) | Candidate order shuffled per round via module-global `random` | **Yes** (one `shuffle` per round) |
| `GRAPHIA_NIGHT_ROSTER_SHUFFLE` explicitly falsy | Prior `players`-dict insertion order | **No** — byte-for-byte prior trajectory |

---

## 3. Impact and Risk Analysis

### 3.1 The load-bearing risk: RNG consumption shifts seeded trajectories

This is the determinism finding the spec flags as the key bit.

Graphia's mechanical decisions all draw from one shared module-global `random` state (architecture §6). The order in which draws happen is what a fixed seed pins. **Inserting a new `random.shuffle` into the Night flow consumes RNG state**, so in a seeded run every subsequent draw (the per-round Mafioso shuffle `_shuffle_mafia_order`, the night-kill tie-break `random.choice`, the AI-pointing random fallback, the next Day's `_shuffle_order`, future role-deal/tie-breaks) is shifted relative to a build without the shuffle. Concretely:

- **(a) OFF must take no draw — non-negotiable.** When the flag is OFF, `_shuffle_night_roster` must return the input list **without calling `random.shuffle` (or any RNG API) at all**. A guard like `if not enabled: return list(candidates)` placed *before* the shuffle call is the contract. If OFF still drew (e.g. shuffled then discarded, or always shuffled then conditionally used), it would shift the global-RNG trajectory and break the "OFF reproduces prior behavior byte-for-byte" promise — defeating the entire point of the ablation flag. **This is the single most important invariant to test.** (Verified at design level only — there is no existing automated check that OFF preserves a prior seeded transcript byte-for-byte; §4 adds one.)
- **(b) ON must be reproducible under a fixed seed.** Because the shuffle uses the module-global `random` API (not a private `Random()` instance, not `os.urandom`), seeding the global RNG before the run fully pins the shuffled order — exactly as `test_slice_day_order_fairness.py` pins `_shuffle_order` via `random.seed(child)`. ON is therefore reproducible-under-seed and free-varying-unseeded, satisfying both functional ACs under "Reproducible under a seed."

### 3.2 The dual-mode byte-equal smoke (`tests/test_dual_mode_smoke.py`) — analysis

`test_local_and_remote_full_game_produce_identical_public_output` runs the SAME game twice (local then remote), each preceded by `random.seed(SEED_DUAL_MODE_DETERMINISTIC_TRAJECTORY)` (seed 0), and asserts byte-identical public log + `kill_log` + `winner`. The relevant question for this spec: **does adding the Night-roster shuffle keep that test green?**

Finding — **yes, it stays byte-equal, provided the seam is mode-independent** (which it is by construction — the shuffle lives in shared `night.py` node code reached through the shared `_assemble_graph`, identical in both builders). The two runs are made comparable by three things the test already controls: (1) `random.seed(0)` reset before *each* run, (2) scripted LLM via fakes, (3) scripted human input. With the flag at its default (ON), **both runs draw the new shuffle at the same point from the same seeded global RNG, producing the same order and the same downstream draws** — so the byte-equal assertion holds. The shuffle is mode-independent: nothing about local vs remote changes how or when `_shuffle_night_roster` draws.

Two narrower observations:

- The seed-0 trajectory currently puts the human in slot 0 as Law-abiding "that needs no `kind="point"` interrupt answer" — i.e. the human is a Law-abiding target, not a Mafioso pointer, so `mafia_point` never interrupts in this test. The shuffle's *interaction with the human interrupt* (the §2.2 replay concern) is therefore **not exercised** by the dual-mode smoke; it is covered by a dedicated test (§4). The dual-mode smoke does exercise the AI-only pointing path with the shuffle active.
- The shuffle changes *which* Law-abiding player the Mafia target at seed 0 relative to today's baseline (the new draw shifts the trajectory). That is expected and fine: the dual-mode test asserts local≡remote, **not** equality against any pre-030 transcript. The test does assert a *non-empty* `kill_log` and a *decisive* winner (`law_abiding`/`mafia`/`draw`); the maintainer must confirm seed 0 with the shuffle ON still yields a decisive, non-stuck game. If seed 0 happened to degrade into a runaway/draw with the shuffle on, the fix is to pick a new documented seed (the test's own comment already anticipates seed choice is load-bearing) — **not** to special-case the flag in the test. (Could not run the suite per task constraints; this is a design-level prediction the maintainer should confirm on first green run.)

### 3.3 Other existing seeded/trajectory tests

Any existing test that (i) seeds the global RNG and (ii) drives a Night with ≥1 living Mafioso and ≥1 living Law-abiding target will have its trajectory shifted when the flag is ON, because the new draw consumes RNG before the test's expected downstream draws. Candidate-affected suites (those that monkeypatch `_shuffle_mafia_order` / drive Night pointing): `test_multi_round_consensus.py`, and any Night-driving slice test. Mitigations, in order of preference:

- **Monkeypatch the new seam to identity**, exactly as tests already monkeypatch `_shuffle_mafia_order` / `_shuffle_order` / `_shuffle_deck` to pin order. A test that pins the Mafioso order should also pin `_shuffle_night_roster` to identity so its expected target/trajectory is stable and intent-readable (architecture §6: "pin it via targeted monkeypatching of the RNG-using helper"). This is the recommended fix and matches the established pattern.
- **Or** set the flag OFF in tests that assert the legacy insertion-order trajectory (env or config), which by §3.1's contract takes no draw and preserves the exact prior behavior.

Either keeps the suite green; the monkeypatch-to-identity route is preferred because it reads as intent and is immune to the flag's default flipping.

### 3.4 System dependencies & non-impacts

- **Depends on:** the module-global `random` mechanical-RNG seam (architecture §6); the spec-015 `mafia_round_start`/`mafia_point` super-step structure (for replay-safe placement); the ADR-011 flag-threading machinery in `config.py` + `_assemble_graph` + `build_runtime_graph`.
- **No impact on:** candidate eligibility (`_alive_law_abiding` unchanged), the multi-round agreement mechanic, `resolve_night_kill` tally/plurality/tie-break, win conditions, the Day roster (explicitly out-of-scope — the all-players Day list is a separate follow-up surface), prompt wording (only line *order* in `_roster_lines` changes), the diary/career stores, the checkpoint serde.
- **Cross-spec shared surface:** This spec and **spec 028 (Per-AI Day-Round Private Thoughts)** both add a new default-on flag and so both touch `config.py` (new `GraphiaConfig` field + `_env_flag` line) and `graph.py` (new `_assemble_graph` kwarg + dual-builder threading). The two specs are **NOT disjoint** on those two files — whichever lands second must rebase its config-field/threading additions onto the first (additive, no conflict in logic, but textually adjacent edits). Independent of spec 029 (a view-ledger/metrics change, no overlap).

### 3.5 Potential risks & mitigations

| Risk | Mitigation |
| --- | --- |
| OFF still draws RNG, breaking prior-behavior reproducibility | Guard returns input list before any RNG call; dedicated no-draw test (§4). |
| Shuffle re-runs on human-pointer interrupt replay (different/extra draw) | Place the draw in an interrupt-free super-step (`mafia_round_start`, Option A), freeze the order in state; `mafia_point` only reads it. |
| AI and human pointers see different orders within one super-step | Apply the single reordered list to both the `_roster_lines` render and the `options` payload at one assembly point. |
| Dual-mode smoke (seed 0) degrades to a non-decisive game with shuffle ON | Confirm on first green run; if needed pick a new documented seed (test already treats seed choice as load-bearing) — do not special-case the flag. |
| Existing seeded Night tests shift trajectory | Monkeypatch the new seam to identity (preferred) or set flag OFF in those tests. |
| Config/threading textual overlap with spec 028 | Additive edits; second-to-land rebases its field + kwarg onto the first. |

---

## 4. Testing Strategy

All assertions are structural / order-based, never verbatim LLM text (architecture §6). No real Bedrock — the autouse `safe_llm` net plus the LLM-boundary fakes apply; the seam itself is pure stdlib RNG so most tests need no LLM at all. Mirror `tests/test_slice_day_order_fairness.py` (the `_shuffle_order` fairness suite) and the spec-024 flag-parity shape (`tests/test_role_guidance.py`).

- **Set preserved (order may change):** drive `_shuffle_night_roster` with a fixed candidate list under a seeded global RNG; assert the returned **set of ids equals** the input set (none missing, none extra) and `len` is preserved. Covers functional AC "contains exactly those players."
- **Order is actually shuffled (via the monkeypatchable seam):** over many seeds (the `_child_seeds(BASE_SEED, N)` + `random.seed(child)` harness from `test_slice_day_order_fairness.py`), assert the helper produces `> 1` distinct order for a fixed candidate set — proving the shuffle does reorder, and proving the seam is the single monkeypatch point that pins it.
- **Role/type independence:** assert the produced order does not depend on `role` / `is_human` (reassign those to different ids, same ids+alive set, same seeds ⇒ byte-identical order sequences) — the fairness invariant `_shuffle_order` already holds.
- **Flag-OFF = insertion order, NO draw (the load-bearing test):** with `enabled=False`, assert `_shuffle_night_roster` returns the exact input order; **and** assert it takes no RNG draw — e.g. record the global RNG state (`random.getstate()`) before/after and assert it is unchanged, or monkeypatch `random.shuffle` to raise and assert OFF never calls it. This is the §3.1 contract that guarantees OFF reproduces prior seeded behavior byte-for-byte.
- **Seed-reproducible (ON):** with `enabled=True`, `random.seed(S)` then call the helper twice with a re-seed between ⇒ identical order under the same seed; different seeds ⇒ (statistically) different orders. Covers functional AC "fixed seed ⇒ reproduced identically; no seed ⇒ varies."
- **`load_config()` default-on semantics:** unset / truthy `GRAPHIA_NIGHT_ROSTER_SHUFFLE` ⇒ `night_roster_shuffle_enabled is True`; explicit falsy ⇒ `False` (mirror the `GRAPHIA_ROLE_GUIDANCE` config test).
- **Threading anti-drift:** assert `build_runtime_graph` accepts and forwards `night_roster_shuffle_enabled` into `_assemble_graph` (mirror the spec-024 `build_runtime_graph` threading assertion), so local and remote can't drift.
- **Replay-safety (if Option A):** drive a Night with a **human Mafioso** pointer to the `"point"` interrupt, resume, and assert the candidate order shown was computed once (the frozen `night_law_order`), not re-drawn on resume — i.e. the order in the resumed `options`/render equals the order before the interrupt, and no extra RNG draw occurred across the replay. (This is the path the dual-mode smoke does *not* cover — §3.2.)
- **Dual-mode smoke unchanged:** `tests/test_dual_mode_smoke.py` should pass as-is with the flag at default ON (§3.2); confirm on first green run. Do not edit the test to special-case the flag.

---

## 5. Open Decisions

1. **Replay-safety placement (§2.2): Option A (shuffle in `mafia_round_start`, freeze a new `night_law_order` state channel, read in `mafia_point`) vs. an inline-in-`mafia_point` draw.** Recommendation is **Option A** — it is the only placement that satisfies the existing spec-015 §3 replay-safety invariant without a fragile draw-before-interrupt argument, and it lets the flag ride the same node as the draw (cleaner threading). Needs sign-off because it adds one state channel.
2. **Flag / env-var name.** `GRAPHIA_NIGHT_ROSTER_SHUFFLE` (field `night_roster_shuffle_enabled`) and helper name `_shuffle_night_roster` are provisional, chosen to read as "the Night roster order is shuffled." Confirm naming against the `GRAPHIA_<FEATURE>` convention before implementation.
