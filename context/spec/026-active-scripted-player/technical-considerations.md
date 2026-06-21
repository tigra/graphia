# Technical Specification: Active Scripted Player for Measured Runs

- **Functional Specification:** [`context/spec/026-active-scripted-player/functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

Today the human seat in a measured (eval) run is filled by a **passive** stand-in baked directly into the blunder-eval drive loop (`blunder_eval._play_one_game`): on a `day_turn` interrupt it returns a neutral `HUMAN_LINES` speech; on a `vote` interrupt it always returns `"no"`; on a `point` interrupt it returns the first option — and the human is dealt law-abiding (`GRAPHIA_ROLE` defaults to `law-abiding`), so the `point` interrupt rarely even fires. As the 024+025 transcript investigation (run `2026-06-20T21-15-30`) showed, this passive seat is now the arithmetic constraint on whether an AI town can ever convict (the permanent 3-No block stalls a *correct* vote at 3–3).

This change introduces a **new, pure, deterministic scripted-player policy module** that supplies the human seat's three resume values in place of the passive defaults: the `day_turn` action (speak text **or** a `/vote <name>` initiation), the `collect_votes` ballot (Yes/No), and — when the seat is dealt Mafia — the `mafia_point` night target. The policy reasons over the **public game so far** (plus, for a Mafioso, its legitimately-known teammates), with **no LLM call and no RNG** (deterministic tie-breaks by player id), so a measured game stays reproducible per-seed and adds no token cost.

The policy is selected by a **default-on boolean ablation flag** (`GRAPHIA_ACTIVE_SCRIPTED_PLAYER`, default on = active; per [ADR 011](../../adr/011-ablatable-gameplay-feature-flags.md), consistent with the other flags' `_env_flag` shape) with a readable blunder-eval CLI override (`--scripted-player active|passive`), and the chosen mode is **recorded into each ledger record's `settings.scripted_player`** (a readable `active`/`passive` label) so records stay self-describing across the deliberate baseline shift (every committed baseline is `passive`; the new default is `active`).

**Systems affected:** only the eval harness surface — `src/graphia/tools/` (a new policy module + the `blunder_eval` drive loop, CLI, and ledger `settings`) and `src/graphia/config.py` (one new resolved setting). **No production game code changes** (no node, schema, graph, or prompt edits): the verified finding (§3) is that the human-seat resume protocol *already* supports both a `day_turn` vote-initiation and a Mafioso night point — the eval driver simply never exercised them.

This spec is independent of specs 024 (Role Guidance) and 025 (Context Window), which change the **AI players' prompts**; this change touches only the **human seat's** automated stand-in. They compose without interaction.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 New module: `src/graphia/tools/scripted_player.py`

A pure, dependency-light policy module (stdlib + `graphia.state` / `graphia.prompts` imports only — **no `graphia.llm` import**, which structurally guarantees the no-model-call acceptance criterion). It reconstructs a public view of the game from the same signals the metric scorers already parse, scores living players, and returns one decision per call.

Responsibilities (function-level contracts; no full bodies):

- **`reconstruct_public_view(messages, players, human_id) -> PublicView`** — walk the game's `messages` history (the same `SystemMessage` Moderator lines the blunder scorers parse) and build a `PublicView` dataclass of *public-only* facts:
  - **Confirmed sides** — from the public reveals only:
    - executions, via `VOTE_EXECUTED_TEMPLATE` (`"{name} has been executed. {name} was a {role_label}."`) → the executed player's name + revealed side (`_role_label`: `"Mafioso"`/`"Mafia"` ⇒ mafia, else law-abiding);
    - night-kill victims, via `DAY_OPEN_VICTIM_REVEAL_TEMPLATE` (`"Day breaks. {name} was killed last night. {name} was a {role_label}."`) → confirmed **Law-abiding** (a night victim is always law-abiding in this game).
  - **Vote behaviors per player** — from `VOTE_INITIATE_ANNOUNCE_TEMPLATE` (initiator + target) and `VOTE_PER_BALLOT_TEMPLATE` (voter + Yes/No), attributed to the most-recent announce's target exactly as `score_vote_blunders` does (one active vote at a time; ballots bind to the open target until the next announce).
  - **Night-kill victims' prior targets** — for each night victim, the players that victim had earlier *moved against* (initiated a vote on, or voted Yes on) → those actors gain suspicion ("Mafia silence their hunters").
  - The regex anchors are **derived from the imported `graphia.prompts` templates** (reuse `blunder_eval`'s template-coupling discipline — ideally lift the shared `_template_to_regex` / `_name_index` helpers, or import them), so a template reword breaks the offline tests loudly rather than silently mis-reading sides.
  - **Knowledge-boundary invariant (load-bearing):** the law-abiding view derives confirmed sides ONLY from these public reveals — it must **never** read `players[*].role` for a living, unrevealed player. (See §2.5.)

- **`score_suspicion(view, *, against_perspective) -> dict[player_id, float]`** — a per-living-player suspicion score from the public signals against confirmed sides. Higher = more suspect (more likely Mafia / less town-aligned). Proposed starting weights (all **tunable**, marked as module constants):

  | Public signal (per actor) | Δ suspicion | Rationale |
  |---|---|---|
  | **Proposed** execution of a **confirmed Mafioso** | **−3.0** | strongest town-aligned signal; weighted above a follow-on Yes |
  | **Voted Yes** on a **confirmed Mafioso** | −1.5 | town-aligned, but less than initiating |
  | **Proposed** execution of a **confirmed Law-abiding** | +3.0 | actively pushing an own-goal ⇒ suspicious |
  | **Voted Yes** on a **confirmed Law-abiding** | +1.5 | helped an own-goal |
  | **Spared** (voted No) a **confirmed Mafioso** | +1.5 | protected a known Mafioso ⇒ suspicious |
  | **Spared** (voted No) a **confirmed Law-abiding** | −0.5 | correctly declined an own-goal ⇒ mildly town-aligned |
  | Was a **prior target** of a **night-killed** player | +1.5 | the Mafia silence their hunters |

  Confirmation is **retrospective**: a vote cast before a side was revealed is scored once that side becomes confirmed (the walk resolves sides first, then attributes behaviors). A dead player carries no live score (it is dropped from the returned map). Starting threshold and selection rule below.

- **`law_abiding_decision(view, scores, *, kind, last_round, open_vote_target) -> Decision`** — the LA policy:
  - **`day_turn`**: state the noted facts / current top suspicion as the speech text (so the transcript shows the reasoning, e.g. `"X was revealed Mafia; I most suspect Y."`). On the **final discussion round of the Day** (`last_round`), instead return a **vote-initiation** on the highest-suspicion living non-self player.
  - **`vote`** (open ballot on `open_vote_target`): **Yes** iff `scores[open_vote_target] >= SUSPICION_THRESHOLD`, else **No**.
  - **Selection + tie-break:** highest suspicion wins; ties broken **deterministically by player id** (lexical), then seat/roster order — never RNG.
  - Proposed starting **`SUSPICION_THRESHOLD = 1.0`** (tunable): a player must carry at least one net-suspicious public signal to be voted out, so an all-quiet table yields No rather than a coin-flip own-goal.

- **`mafia_decision(view, scores, *, kind, last_round, open_vote_target, teammate_ids, self_id) -> Decision`** — the Mafioso policy (push-parity, protect-teammates):
  - **Target choice** = the strongest-hunter Law-abiding: among **living non-teammates**, the one the *same public scoring* marks **most town-aligned** (lowest suspicion = most actively-correct-anti-Mafia), i.e. `argmin(scores)` over non-teammates; deterministic id tie-break. This is the biggest threat to Mafia cover.
  - **`day_turn`**: voice a (deceptive) suspicion of that target as speech text; on the final round, return a **vote-initiation** on the target.
  - **`vote`**: **Yes** on any **non-teammate** open target; **No** (spare) on any **teammate** open target — unconditionally (teammate protection overrides the suspicion threshold).
  - **`point`** (night): return the same chosen-target id (the strongest hunter). Never points at a teammate by construction (the candidate set is non-teammates only).
  - **Never reveals** side or teammate names (the speech text is suspicion-of-target only; no teammate is ever named).
  - **Teammate knowledge is legitimate:** unlike the LA path, the Mafioso MAY read `teammate_ids` (the other `role=="mafia"` players) — it knows them from the private teammate intro and its own seat.

- **`Decision`** — a small dataclass discriminated by the interrupt kind it answers: for `day_turn`, either `("speak", text)` or `("vote", target_id)`; for `vote`, a bool; for `point`, a `target_id`. The driver maps it to the existing resume protocol (§2.3).

### 2.2 Config: `src/graphia/config.py`

One new resolved setting following the existing ablation-flag precedent (`recap_aware_reasoning_enabled` / `role_guidance_enabled`):

- A `GraphiaConfig` field `scripted_player_active: bool = True` (defaulted, like the other ablation flags, so direct test construction stays valid).
- Resolved in `load_config()` via `_env_flag("GRAPHIA_ACTIVE_SCRIPTED_PLAYER", default=True)` — the **default-on boolean** shape shared with `GRAPHIA_DAY_ROUND_RECAP` / `GRAPHIA_RECAP_AWARE_REASONING` / `GRAPHIA_ROLE_GUIDANCE`; an explicit falsy value selects the **passive** baseline (byte-for-byte prior behaviour). The blunder-eval `--scripted-player active|passive` CLI overrides it for a run (a readable two-value surface mapping to the bool), and the readable `active`/`passive` label is what the ledger records (§2.4).

  > **Resolved (D1):** modeled as the **default-on boolean** `GRAPHIA_ACTIVE_SCRIPTED_PLAYER` (consistent with the other ADR-011 flags); the readable `active`/`passive` labels live only at the CLI + ledger edges.

### 2.3 Integration: `src/graphia/tools/blunder_eval.py`

The drive loop in `_play_one_game` is the single integration site. Changes:

1. **Construct the policy once per game**, after roles are dealt (right where the capture proxy is installed — `players_now = graph.get_state(run_config).values.get("players", {})`). At that point the human seat's dealt role and teammates are known: read `human_id = state["human_id"]`, `human = players_now[human_id]`, `human.role`, and (if Mafia) the other `role=="mafia"` ids as `teammate_ids`. This is the **only** place the policy is allowed to read true roles — for the human seat's **own** role and its **own** teammates, which is legitimate self-knowledge, not cheating.

2. **Replace the three resume branches** (gated on `config.scripted_player_mode`):
   - `kind == "day_turn"` → in **active** mode, call `reconstruct_public_view` over the live `graph.get_state(...)` `messages` + `players`, score, and call the role-matched decision. A `("speak", text)` decision resumes with the text (exactly the shape the human speech path accepts); a `("vote", target_id)` decision resumes with the string **`f"/vote {name}"`** (the human `/vote` slash-command branch fuzzy-matches the name → see §3). In **passive** mode, the current `HUMAN_LINES[line_idx % …]` path is kept verbatim.
   - `kind == "vote"` → **active**: resume `"yes"`/`"no"` from the ballot decision; **passive**: keep the literal `"no"`.
   - `kind == "point"` → **active** (only reached when the seat is Mafia): resume the policy's chosen target id; **passive**: keep `options[0]["id"]`.
   - The "last discussion round of the Day" signal the LA/Mafia policies need is read from the **public state** at decision time: the `day_turn` interrupt payload already carries `speaker_id`/`alive_names`, and the live `graph.get_state(...).values` exposes `day_rounds` (completed rounds) and `DAY_MAX_ROUNDS` (= 6). "Final round" = the speaking turn during round `DAY_MAX_ROUNDS`. (Resolved D2, per the spec's explicit "at the last round"; proposing earlier when `day_votes_initiated == 0` is a noted tunable, not the default.)

3. **Role of the seat.** `main()` currently forces `os.environ.setdefault("GRAPHIA_ROLE", "law-abiding")` so the human never faces a `point` interrupt. For the active player to be exercised **as a Mafioso**, the seat's role becomes a **per-run selectable value, default `law-abiding`** (Resolved D3): the default keeps the primary town-win measurement working out of the box, and a separate `GRAPHIA_ROLE=mafia` batch exercises and cleanly attributes the Mafioso policy. The driver already has a `kind == "point"` branch, so no protocol change — only the unconditional law-abiding pin becomes a per-run default.

4. **No new model call.** The capture proxy (`_install_capture_provider`) wraps the real provider for the AI players; the scripted seat must **not** route through it. Because the policy module never imports `graphia.llm` and the driver computes the resume value directly (not via an interrupt that re-enters a node), the seat adds zero invokes — preserved as a test assertion (§4) and structurally by the no-`llm`-import rule.

### 2.4 Ledger recording: `settings.scripted_player`

Record the chosen mode in each run's `settings` block so records self-describe across the baseline shift:

- In `run_eval`, add `"scripted_player": <mode>` to the `settings` dict (beside `games`, `seed`, `max_days`, `lineup`).
- In `render_record`, emit it as a flat `settings` scalar (e.g. after `max_days`, before the nested `lineup`), via the existing `_yaml_block`. It is a plain string — `_yaml_scalar` quotes it.
- **Additive / back-compatible:** a synthetic/older `settings` map without the key renders without it (`settings.get("scripted_player")` → omit when `None`, exactly as `lineup` is conditionally rendered). The `evals/README.md` `settings` field-legend gains one line; **no `metrics_version` bump** — this is a settings/provenance addition, not a detection-rule change (the `ci_low`/`outcomes`/`lineup` precedent).
- Update `evals/README.md`'s record-shape legend and the `settings` field-by-field entry to document the new key and that **existing committed baselines are all implicitly `passive`** (the field is absent on pre-026 records — read as `passive`).

### 2.5 Knowledge-boundary invariant (the load-bearing correctness rule)

This is the single most important property and mirrors the existing spec-013 §2.5 role-knowledge boundary already enforced in `nodes/day.py` (`_team_line` / `_teammates_str`) and `nodes/night.py`:

- The **Law-abiding** policy's `PublicView` is built **exclusively** from public Moderator reveal lines in `messages`. It must never consult `players[pid].role` for a living, unrevealed player. Enforced two ways: (a) `reconstruct_public_view` for the LA path takes the `messages` log + only the *names/ids/alive-flags* it needs, never reading `.role` of unrevealed players; (b) a dedicated unit test (§4) feeds a history whose hidden true roles **contradict** what the public reveals imply and asserts the LA scores follow the public reveals, not the hidden truth.
- The **Mafioso** policy MAY read its own `teammate_ids` and `self_id` (legitimate self-knowledge from the private teammate intro). It still scores all *other* players from public signals only — it does not get to see other living players' hidden sides either; it simply additionally knows which players are its own team.

### 2.6 Logic / determinism summary

- **No RNG, no clock, no LLM** anywhere in `scripted_player.py`. Every choice is a pure function of the reconstructed public view (+ the Mafioso's teammate set). Tie-breaks are lexical-by-id then roster order. This keeps the seeded-structure reproducibility of the harness intact (architecture §6: mechanical RNG is for the *game engine*; the scripted seat deliberately adds none of its own).
- The policy is **driver-independent and pure** — it takes plain `messages` + `players` + the interrupt payload's facts and returns a `Decision`, exactly like the existing `score_*` scorers take plain inputs. That is what makes it precisely unit-testable offline with synthetic histories built from the real templates (§4).

---

## 3. Impact and Risk Analysis

### VERIFIED integration finding (the load-bearing risk)

I verified the human-seat resume protocol against `nodes/day.py`, `nodes/night.py`, and `blunder_eval._play_one_game`:

- **(a) Vote-INITIATION from `day_turn` — SUPPORTED, no protocol change.** `day_turn` (in `nodes/day.py`) handles a human turn that begins with `/vote <name>`: it strict-matches the `/vote` token, fuzzy-matches the remainder to an alive player via `_fuzzy_match_alive`, and on success calls `_begin_vote(...)` setting `active_vote` and routing to `vote_prompt`. So a human-seat resume of the string `"/vote <name>"` already initiates a vote. **What's missing is purely on the eval-driver side:** `_play_one_game` only ever resumes a `day_turn` interrupt with a `HUMAN_LINES` speech string — it never emits a `/vote`. The fix is entirely in the driver (resume with `f"/vote {name}"`); the resume value is a fuzzy **name** match, so the policy must pass the target's display **name**, not its id, on this path.
- **(b) Mafioso night point for the human seat — SUPPORTED, no protocol change.** `mafia_point` (in `nodes/night.py`) interrupts with `{"kind": "point", "options": [{id, name}…], …}` for a human Mafioso and accepts a resumed `target_id` string (validated against `valid_ids`, else a defensive fallback). `_play_one_game` already has a `kind == "point"` branch (it picks `options[0]["id"]`). **What's missing:** the eval `main()` pins the human seat to `law-abiding` (`GRAPHIA_ROLE` setdefault), so the `point` interrupt almost never fires today. To exercise the Mafioso policy the role pin must be relaxed (§2.3 step 3) and the branch must return the policy's chosen id instead of `options[0]`.

**Conclusion:** **no production-code/protocol change is required** for either capability — both are already on the human-seat happy path. The work is (1) the new policy module, (2) the driver swapping in policy-computed resume values, and (3) relaxing the law-abiding role pin so the Mafioso path is reachable. I could not (and did not) run any game to observe this live — the finding is from reading the three files; it should be confirmed at implementation by a smoke run that asserts a scripted `/vote` actually opens a vote and a scripted point lands a kill.

### System dependencies

- **Template coupling.** The policy parses the same Moderator reveal/vote templates the blunder scorers parse (`VOTE_EXECUTED_TEMPLATE`, `DAY_OPEN_VICTIM_REVEAL_TEMPLATE`, `VOTE_INITIATE_ANNOUNCE_TEMPLATE`, `VOTE_PER_BALLOT_TEMPLATE`). Reuse the template-derived-regex discipline (and ideally the shared `_template_to_regex` / `_name_index` helpers) so a reword fails offline tests loudly. Risk: a future template change that the policy parser doesn't track → the LA view silently loses a confirmed side. Mitigation: derive anchors from the imported templates, plus the contradiction test (§4).
- **State fields read:** `messages`, `players`, `human_id`, `human_role`, `day_rounds` (+ `DAY_MAX_ROUNDS`/`DAY_MAX_VOTES` constants), `active_vote` (target on the open ballot), `day_votes_initiated`. All already-public, all already in `GameState`.

### Potential risks & mitigations

- **Baseline shift (default active → future records differ from committed passive baselines).** Acknowledged and intentional. Mitigated by recording `settings.scripted_player` in every record (§2.4) and documenting in `evals/README.md` that pre-026 baselines are implicitly `passive`. Re-scoring already-recorded games is explicitly out of scope (functional spec §3). A reader compares active-vs-passive by the recorded mode, never by assuming.
- **Determinism / reproducibility.** No RNG/LLM in the policy ⇒ same public history yields the same decision; the per-seed game *structure* reproducibility is unaffected. The only non-determinism in a measured game remains the AI players' LLM dialogue (the thing being measured) and the engine's own mechanical RNG — both untouched. Asserted by a same-history-same-decision test (§4).
- **`safe_llm` net is irrelevant here but must stay honored.** The policy makes no model call, so the autouse `safe_llm` fixture has nothing to guard for this module. The real risk it *would* catch — a stray real-Bedrock call — is structurally prevented by the no-`graphia.llm`-import rule. A test asserts zero invokes for a scripted seat (§4); and any new module that *did* call an LLM would need adding to `safe_llm` — this one deliberately does not.
- **Cross-spec independence (024/025).** Different surface entirely: 024/025 change the AI players' prompt assembly in `nodes/day.py`; this changes the human seat's resume values in the eval driver. No shared code path, no flag interaction. They compose; an active-seat run can be combined with any 024/025 flag setting.
- **Mafioso never-reveal regression risk.** The Mafioso speech text is generated by the policy (suspicion-of-target only, never naming a teammate or its own side). A test asserts no teammate name and no side word appears in any Mafioso scripted speech, and that it never proposes/points/Yes-votes a teammate (§4).
- **Self-vote / self-point safety.** The LA selection excludes self by construction (the seat does not score itself as a target to propose); the Mafioso candidate set is non-teammates (and the seat is its own teammate-set member, so self is excluded). Mirrors `day_turn`'s own `target_id != speaker.id` guard. Asserted by test.

---

## 4. Testing Strategy

The policy is **deterministic and pure**, so it is precisely unit-testable offline (no live model, no RNG) — the same posture as the existing `score_*` scorers in `blunder_eval`. New test file, e.g. `tests/test_slice26_scripted_player.py` (slice-numbered per the project convention), building synthetic `messages` histories from the **real `graphia.prompts` templates** (so a reword breaks the test loudly) plus a small `players` map.

Test intents (asserting the functional-spec acceptance criteria):

- **LA scoring weighting (FR §2, AC1):** given an execution that revealed a Mafioso, a player who **proposed** that execution scores more town-aligned than one who merely **voted Yes**, and both more town-aligned than one who **voted to spare** — assert the ordering of the suspicion scores, not exact magnitudes (so the weights stay tunable).
- **LA own-goal suspicion (AC2):** a player who proposed/Yes-voted someone later revealed **Law-abiding** scores **more suspicious**.
- **Night-killed victim's hunters (AC3):** a player the night victim had earlier moved against gains suspicion.
- **Knowledge-boundary (load-bearing):** feed a history where the **hidden true roles contradict** what the public reveals imply; assert the LA scores follow the **public reveals**, never the hidden `players[*].role` of unrevealed players (the cheat-proof test).
- **LA final-round proposal (AC4):** on the final discussion round, the LA decision is a **vote-initiation on the highest-suspicion living player** (with the deterministic id tie-break asserted on a constructed tie).
- **LA ballot (AC5):** open vote on a suspected target (score ≥ threshold) ⇒ **Yes**; on a trusted target ⇒ **No**.
- **LA states facts (AC6):** any `day_turn` speak decision's text mentions a noted fact / the top suspicion (assert the suspect's name appears, not verbatim text).
- **Mafia teammate protection (Mafia AC1, AC4):** a teammate put up ⇒ **No**; the Mafioso never proposes / night-points / Yes-votes a teammate; never names a teammate; never reveals its side (no side word in speech).
- **Mafia push (Mafia AC2):** a non-teammate put up ⇒ **Yes**.
- **Mafia target choice + night point (Mafia AC3):** the chosen target is the **lowest-suspicion living non-teammate** (strongest hunter); on the final round the Mafioso proposes that player and the `point` decision returns that same id; deterministic id tie-break asserted.
- **Determinism (FR §2 "no-model", AC1+AC2):** same synthetic history ⇒ identical decision across repeated calls; and a test that constructing/using the policy issues **zero LLM invokes** (e.g. assert the `safe_llm` loud-failure LLM is never touched, or assert the module imports without `graphia.llm`).
- **Flag default + override:** `load_config()` with no env ⇒ `scripted_player_active is True`; `GRAPHIA_ACTIVE_SCRIPTED_PLAYER=0` ⇒ `False`; the `--scripted-player passive` CLI override selects passive; an invalid `--scripted-player` value errors.
- **Passive parity (ADR 011 flag-off parity test):** in `passive` mode the driver's resume values are byte-identical to today's (`HUMAN_LINES` speech, `"no"` ballot, `options[0]` point) — a focused test on the driver's resume-selection helper, factored out so it is testable without a live game.
- **Ledger records the mode:** `render_record` over an `EvalResult` whose `settings["scripted_player"]` is set emits the `settings.scripted_player` line; an `EvalResult` without it omits the key (back-compat) — pure-render test, no live run.

**Out-of-suite (effort-not-results, [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md)):** the real-model win-rate effect — whether an active LA seat lets the AI town break the 3–3 deadlock and convict, and whether the AI Mafia can beat an active LA seat — is **not** a pytest assertion. It is a measured `make blunder-eval` comparison: run active vs the recorded passive baseline (e.g. n=20 per side), read `outcomes` (win-rate by side, Wilson CI) and the share of correct-vs-own-goal executions, and log the hypothesis confirmed or refuted in the run `--note` / a follow-up. Either outcome is a complete result.

---

## Resolved decisions

1. **D1 — flag shape → default-on boolean.** `GRAPHIA_ACTIVE_SCRIPTED_PLAYER` (default on = active) via `_env_flag`, consistent with the other ADR-011 flags; the readable `active`/`passive` labels appear only at the CLI (`--scripted-player`) and ledger (`settings.scripted_player`) edges.
2. **D2 — "final round" → round `DAY_MAX_ROUNDS`.** Per the spec's explicit "at the last round," the vote-proposal fires on the speaking turn during round `DAY_MAX_ROUNDS` (read from public `day_rounds`). Proposing earlier when `day_votes_initiated == 0` is a noted tunable, not the default.
3. **D3 — role pin → per-run selectable, default law-abiding.** The seat's role is a per-run value defaulting to `law-abiding` (so the primary town-win measurement works out of the box); run a separate `GRAPHIA_ROLE=mafia` batch to exercise and cleanly attribute the Mafioso policy.
