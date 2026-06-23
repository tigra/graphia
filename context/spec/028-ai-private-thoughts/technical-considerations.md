# Technical Specification: Per-AI Day-Round Private Thoughts

- **Functional Specification:** [028 — Per-AI Day-Round Private Thoughts](./functional-spec.md)
- **Status:** Completed *(verified 2026-06-23 — effort-not-results measurement recorded in the 2026-06-22 ledger runs; [CR 005](../../change-requests/005-ai-behaviour-acceptance-effort-not-results.md))*
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

At the close of each Day speaking round, every **surviving AI** player privately
reflects: one `get_large()` call against a new **mild** reflection prompt
(`REFLECTION_SYSTEM` + a user template) yields a short note. The notes
**accumulate per player** in a new `GameState` channel
`private_thoughts: dict[player_id, list[str]]` and are woven — that player's
**own** thoughts, in event order — into a new `{private_thoughts}` slot in the
three AI decision prompts already in the codebase: Day-speech
(`DAY_SPEAK_USER_TEMPLATE`), vote (`AI_VOTE_USER_TEMPLATE`), and Mafioso
Night-pointing (`MAFIA_POINT_USER_TEMPLATE`).

The whole feature sits behind a default-on ablation flag
`GRAPHIA_PRIVATE_THOUGHTS` (ADR 011 shape, via `_env_flag`) → a new
`GraphiaConfig` field → threaded through `_assemble_graph`'s `partial`s in
**both** `build_graph` and `build_runtime_graph`, exactly as specs 019/024/025
thread their flags.

Systems affected (application-side only — no IaC, no new AWS resource):

- `src/graphia/state.py` — one new channel + its accumulating reducer.
- `src/graphia/prompts.py` — `REFLECTION_SYSTEM` + reflection user template; one
  new `{private_thoughts}` slot added to three existing templates.
- `src/graphia/nodes/day.py` — the reflection step (new node, see §2.1) + a
  pure `_private_thoughts_block` builder + threading the flag/value into
  `_ai_day_action` and `_ai_ballot`.
- `src/graphia/nodes/night.py` — threading the player's own thoughts into
  `_ai_pick_target` / the `MAFIA_POINT_USER_TEMPLATE` assembly in `mafia_point`.
- `src/graphia/graph.py` — register the reflection node + edge; bind the flag.
- `src/graphia/config.py` — the flag.
- `src/graphia/tools/eval_transcript.py` — render each thought as a
  private/annotated element.
- `tests/conftest.py` — extend `safe_llm` to net the new reflection call site
  (required; see §3 and §4).

This is a within-game working scratchpad: **not** the long-term diary, **not**
a public message, **never** carrying `private_to` (which would route it into
another reader's context — the opposite of what we want), **never** entering
the public message stream.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The end-of-round reflection step — dedicated node, NOT folded

**Decision: a new dedicated node `day_round_reflect`, not a fold into
`day_turn` / `_round_complete_update`.** Rationale:

- `day_turn` runs **exactly one player's turn per super-step** — this is the
  load-bearing replay-safety contract (a human `interrupt()` in a later turn
  must not re-run AI calls from earlier in the same super-step). Folding a
  fan-out of N reflection calls (one per surviving AI) into the round-wrap
  return of `day_turn` would put N non-deterministic LLM calls in the *same*
  super-step as a speaking turn, breaking that one-call-per-step posture and
  making the reflection vulnerable to re-execution on replay.
- A dedicated node commits its work as its **own super-step**, so the reflection
  LLM calls are checkpointed once and never replayed — the same discipline
  `mafia_round_start` uses for its shuffle and the existing nodes use for their
  non-deterministic work.
- It keeps the reflection logic out of the already-dense `day_turn` /
  `_round_complete_update` path.

**Where it sits in the topology (this is the genuinely fiddly part — see §5
open decision 1).** A speaking round completes when `day_turn` returns a delta
carrying `day_rounds` (the round-wrap signal that `_round_complete_update`
produces). The reflection must fire on the **loop-back** path (round completed,
game continues) but **not** when the completed round is also the Day's end
(round cap → `day_close`). The cleanest fit that preserves the existing
conditional-edge structure:

- Add `day_round_reflect` as a node, then route into it from the existing
  `route_day_turn_or_vote` decision when (a) the delta wrapped a round AND
  (b) the round cap was not hit AND (c) no vote was initiated AND (d) no
  re-prompt error is pending. In all other cases the router behaves exactly as
  today.
- `day_round_reflect` then unconditionally edges back to `day_turn` to start the
  next round.
- The router needs to know "a round just wrapped." `day_rounds` alone isn't a
  reliable single-step signal from inside the router (it's cumulative state, not
  a per-step flag), so the reflection node should instead **fire whenever it is
  routed to and self-guard**: it reflects only when the current cursor is at the
  *start* of a fresh round (`day_turn_index == 0` and `day_rounds >= 1`). The
  router routes to it on the loop-back-and-not-close branch; the node's internal
  guard makes it a safe no-op if conditions don't hold. (The precise router
  predicate vs. node self-guard split is open decision 1.)

**What the node does (responsibilities, no code body):**

- Reads `players`, iterates **surviving, non-human** players (`is_alive and not
  is_human`) — mirroring `night_close`'s diary-write loop and
  `first_night_mafia_intros`. The deterministic scripted eval seat (spec 026) is
  the **human** seat (`is_human=True`), so it is naturally excluded — no special
  case needed. The human is excluded for the same reason.
- For each such player: build the mild reflection prompt (system +
  user template) including that player's role grounding (reuse `_role_label`,
  `_win_condition_line`, `_team_line`), its persona (`_persona_block`), the
  recent discussion (`_render_context` for that player, honoring the same
  `context_window` / `context_token_budget` the Day prompts use), the standings
  (`_render_standings`), and that player's **own** accumulated prior thoughts
  (`_private_thoughts_block` for that player) — so reflection itself is grounded
  in the running train of thought.
- Calls `get_large().with_structured_output(<schema>)` (see §2.5 schema
  decision) once per player, with a try/except + deterministic fallback note so
  one player's model failure never crashes the round (mirrors the
  `_ai_day_action` / `night_close` defensive posture).
- Returns a single state delta writing into the new `private_thoughts` channel,
  keyed per player. **No public `messages`** are emitted (privacy invariant).
- Accepts the ablation flag (`private_thoughts_enabled`) and the
  `context_window` / `context_token_budget` as keyword args bound via `partial`
  in `_assemble_graph`. When the flag is **off**, the node is a pure no-op
  (returns `{}`) and writes nothing — the prompts then revert to their pre-028
  form (see §2.4).

### 2.2 State: the new channel + accumulating reducer

In `src/graphia/state.py`, add to `GameState`:

```
private_thoughts: Annotated[dict[str, list[str]], _merge_private_thoughts]
```

- **Shape:** `dict[player_id, list[str]]` — each player's notes in the order
  written.
- **Reducer (`_merge_private_thoughts`):** a new module-level reducer that
  **accumulates per player** — NOT replace, NOT a plain `dict` merge (which
  would let a later delta's list clobber an earlier one). Contract: given the
  prior map and an incoming delta map, return a new map where each player's list
  is `prior + incoming` (concatenation, preserving event order). This is the
  per-key analogue of `operator.add` on lists; `add_messages` is the precedent
  for a custom reducer, `kill_log`'s `operator.add` the precedent for
  accumulation. It must be a **pure function** (no mutation of inputs — copy
  then extend) so checkpoint replay is stable, and it must not depend on dict
  iteration order semantics beyond insertion order (it never iterates a `set`).
- **Privacy:** this channel is read **only** by the per-player prompt builders
  (each player sees only `state["private_thoughts"].get(player.id, [])`) and by
  the eval-transcript renderer. It is never rendered into a `messages` entry,
  never carries `private_to`, never reaches `_render_context` (which only walks
  `messages`).

### 2.3 Prompts: `REFLECTION_SYSTEM` + reflection user template

In `src/graphia/prompts.py`:

- **`REFLECTION_SYSTEM`** — a new system prompt. Deliberately **mild**:
  invites the player to take stock and plan its own next move in its own voice;
  does **not** prescribe a strategy (explicit contrast to the directive
  `ROLE_GUIDANCE_*` menus of spec 024 — the functional spec says the two
  coexist). Asks for a short private note (one or two sentences), framed as
  thinking for itself, "seen by no one else."
- **`REFLECTION_USER_TEMPLATE`** — slots for: `{speaker}`, `{role_label}`,
  `{win_condition}`, `{team_line}`, `{persona}`, `{standings}`, `{context}`
  (recent discussion), and `{private_thoughts}` (its own prior notes). Mirrors
  the slot vocabulary of `DAY_SPEAK_USER_TEMPLATE` so the same node-side helpers
  populate it. The mildness lives in the wording, not the slots.

### 2.4 Feedback injection: the `{private_thoughts}` slot + its gated builder

Add a new pure builder in `nodes/day.py`:

```
_private_thoughts_block(thoughts: list[str], *, enabled: bool) -> str
```

- Mirrors the established ablation-block shape of `_standings_prompt_block`
  (spec 019) and `_role_guidance_block` (spec 024):
  - `enabled=False` ⇒ returns `""` so the `{private_thoughts}` slot collapses to
    nothing and the prompt is **byte-identical** to its pre-028 form (no label,
    no body, no stray blank line) — the A/B ablation seam and the flag-off
    parity test target.
  - `enabled=True` with no prior thoughts ⇒ returns `""` as well (an AI that
    hasn't reflected yet — e.g. round 1 — adds nothing; first round of play is
    unchanged). (Whether an empty-when-enabled block is exactly `""` or a
    neutral "(no private notes yet)" line is open decision 3.)
  - `enabled=True` with thoughts ⇒ a labelled block listing that player's own
    notes in order (e.g. `"Your private notes so far (yours alone):\n- ...\n- ..."`),
    with framing `\n…\n` chosen so it slots cleanly into the template seam (same
    discipline as `_role_guidance_block`).
- **PURE:** no state read beyond the passed list, no RNG, no LLM, no `set`
  iteration — so the dual-mode byte-equal smoke is unaffected (§3, §4).

Slot placement in the three templates:

| Template | New slot location | Builder call site |
|---|---|---|
| `DAY_SPEAK_USER_TEMPLATE` | a `{private_thoughts}` slot (placement TBD — see open decision 2; likely just before `{role_guidance}` so the player's own reasoning precedes the closing nudge) | `_ai_day_action` |
| `AI_VOTE_USER_TEMPLATE` | a `{private_thoughts}` slot in the same relative position | `_ai_ballot` |
| `MAFIA_POINT_USER_TEMPLATE` | a `{private_thoughts}` slot near the existing `{mafia_persona}` / `{prior_picks}` block | `mafia_point` → `_ai_pick_target` |

Each call site passes **only that player's own** thoughts
(`state["private_thoughts"].get(player.id, [])`) and the `private_thoughts_enabled`
flag. A player never receives another player's thoughts — this is enforced at
the call site by keying on the acting player's id (the same knowledge-boundary
discipline as `_team_line` / `_persona_block`).

`_ai_day_action`, `_ai_ballot`, and `_ai_pick_target` each gain two new
keyword-only params (`private_thoughts: list[str]` and
`private_thoughts_enabled: bool`), defaulted to `([], True)` resp. so existing
direct test calls stay valid (same defaulting convention specs 019/024/025
used).

### 2.5 Reflection structured-output schema

Reuse the project's `with_structured_output` posture (CLAUDE.md: structured
output, not `bind_tools`). Add a small flat Pydantic schema to
`src/graphia/llm.py` — e.g. `Reflection(thought: str)` — kept **flat with a
primitive field** (Bedrock Converse rejects discriminated unions; the existing
`Roster` / `Pointing` / `Ballot` / `DayAction` are all flat). The node accepts
a non-empty `thought`, else falls back to a deterministic placeholder note so
tests aren't flaky and a model hiccup never blanks the channel. (Whether a
dedicated schema is warranted vs. reusing an existing one is minor; a dedicated
`Reflection` reads cleanest and matches the one-schema-per-decision convention.)

### 2.6 Graph wiring (`graph.py`) — both builders

- Register the node: `builder.add_node("day_round_reflect", partial(day_round_reflect, private_thoughts_enabled=..., context_window=..., context_token_budget=...))`.
- Add the loop edge `day_round_reflect → day_turn`.
- Extend the `route_day_turn_or_vote` mapping so the loop-back-without-close
  branch can route to `"day_round_reflect"` (which then edges back to
  `day_turn`), while vote / close / re-prompt branches are unchanged. (Exact
  predicate split is open decision 1.)
- Add a `private_thoughts_enabled: bool = True` parameter to `_assemble_graph`
  and thread `config.private_thoughts_enabled` into it from **both**
  `build_graph` and `runtime/graph_builder.build_runtime_graph` — the same
  "bind in both builders so local and remote can't drift" rule called out for
  specs 018/019/023/024/025 in `_assemble_graph`'s docstring. Also thread the
  flag into the `day_turn` and `collect_votes` partials (they already receive
  `context_window` / `context_token_budget`; add `private_thoughts_enabled`).

### 2.7 Config (`config.py`)

- New `GraphiaConfig` field `private_thoughts_enabled: bool = True` (defaulted so
  tests constructing the config directly stay valid).
- In `load_config`: `private_thoughts_enabled = _env_flag("GRAPHIA_PRIVATE_THOUGHTS", default=True)`
  and pass it into the `GraphiaConfig(...)` constructor. This is the exact
  `_env_flag` default-on shape used by `GRAPHIA_DAY_ROUND_RECAP`,
  `GRAPHIA_RECAP_AWARE_REASONING`, `GRAPHIA_ROLE_GUIDANCE`,
  `GRAPHIA_ACTIVE_SCRIPTED_PLAYER`.

### 2.8 Eval transcript (`eval_transcript.py`)

The reflection node returns a `{private_thoughts: {...}}` delta with **no
`messages`**, so the existing message-walking path (`_append_messages`) will not
surface it. The renderer must learn to read the new channel.

- In `_render_phases`, when a delta carries `private_thoughts`, render each new
  thought as a **private/annotated element attributed to its author**, distinct
  from public speech — e.g. an inline `<thought player="Name">…</thought>` (a
  new tag alongside the existing `<recap>` / `<kill>` / `<vote>` inline shapes
  from spec 022). It lands inside the **current `<round>`** body (the reflection
  fires at the round it summarizes), so a reviewer reads "what the player thought
  at the end of round N" beside "what it said/did."
- **Delta semantics:** the streamed delta is the round's *new* thoughts (the
  reducer accumulates into state, but each super-step's delta carries only that
  step's additions). The renderer should render the delta's per-player lists
  directly rather than diffing the full accumulated map — simplest and matches
  how `_accumulate_night_picks` reads streamed deltas. (Confirm at implementation
  that the `stream_mode="updates"` payload carries the node's *return* delta, not
  the post-reducer merged map; if it carries the merged map, the renderer must
  diff against the prior super-step's map. This is open decision 4 — I could not
  fully verify LangGraph's `updates` payload shape for a custom-reducer channel
  from the code alone.)
- Attribution resolves the player id → name via the existing `names` map.
- Defensive throughout (the renderer's house style): a missing channel, an
  empty list, or an unknown id must never raise — surface what's present, omit
  the rest. **Never** shown to other players or the live human UI — the renderer
  is a maintainer-facing artifact only, which is the only place thoughts surface
  (matching the functional spec).

---

## 3. Impact and Risk Analysis

### System Dependencies

- **Three existing AI-decision prompts** (`DAY_SPEAK`, `AI_VOTE`,
  `MAFIA_POINT`) gain a slot — shares the Day-prompt assembly surface with specs
  019/024/025 (all merged into `_ai_day_action` / `_ai_ballot`). **The
  Mafioso-pointing prompt is touched here for the first time by this family of
  flags** (019/024/025 did not reach into `mafia_point`); the Night injection is
  net-new plumbing on that path.
- **`config.py` + `graph.py` threading is shared with spec 030**
  (Randomized Night-Pointing Roster Order): both add a default-on `_env_flag`
  config field and thread it through `_assemble_graph` in both builders. **028
  and 030 are NOT disjoint on this surface** — both edit `GraphiaConfig`,
  `load_config`, `_assemble_graph`'s signature, and both builders; 030
  additionally touches `mafia_point` / the Night candidate-list assembly, which
  028 also touches for its `{private_thoughts}` Night injection. Whichever lands
  second must rebase its `_assemble_graph` parameter list and the `mafia_point`
  partial onto the first. Flag names are distinct
  (`GRAPHIA_PRIVATE_THOUGHTS` vs. 030's), so no env collision.
- **`safe_llm` (autouse, `tests/conftest.py`)** must be extended — see below.

### Potential Risks & Mitigations

- **Forgotten `safe_llm` stub → real Bedrock (REQUIRED harness change).** The
  reflection node adds a **new `get_large()` call site in `nodes/day.py`**.
  `safe_llm` already patches `graphia.nodes.day.get_large`, so the *binding* is
  covered — **but the unified `FakeLargeUnified` fake has no scripted queue for
  the new `Reflection` schema**, so any test that exercises a full Day round with
  reflection on will hit `FakeLargeUnified.with_structured_output(Reflection)`,
  which raises `AssertionError("no scripted queue for schema Reflection")`.
  Mitigation (REQUIRED): extend `FakeLargeUnified` (and the per-schema dispatch
  in `_LargeQueue`) to carry a `Reflection` queue with the same
  "replay-last-when-drained" behaviour as the other schemas, plus a
  `reflections=` kwarg on the `fake_large` factory. Without this, full-game tests
  with the default-on flag fail loudly (which is the safety net working) — so
  this is a hard prerequisite, not optional. Call it out explicitly in tasks.
- **Cost.** One extra `get_large()` call per **surviving AI** per **completed
  Day round** (rounds 1..5; the cap-boundary round routes to `day_close`, not
  reflection — see §2.1). Worst case at the largest table (~11 AI) over ~5
  rounds/day across several days is a meaningful token bump on the Bedrock path
  and a latency bump on Ollama. Mitigations: the ablation flag turns it off
  wholesale for A/B; the node skips dead players; mildness keeps outputs short
  (one or two sentences). The blunder-eval ledger will measure the win-rate /
  coherence effect against the recorded baseline (effort-not-results).
- **Dual-mode byte-equal smoke (`tests/test_dual_mode_smoke.py`).** See §4 —
  the new node + channel must not break it. The risk is real because the test
  asserts byte-identical public logs across modes; the mitigation is that the
  thought is **private** (no `messages`, no `private_to`), so it never enters
  the `#public-log` the test compares.
- **Replay safety.** Because reflection is its own super-step with N
  non-deterministic LLM calls, a human `interrupt()` on the *next* `day_turn`
  must not re-run them. A dedicated node guarantees this (the calls are
  committed before the next super-step's interrupt). This is the core reason for
  the node-vs-fold decision.
- **Privacy regression.** The single highest-stakes invariant is that a thought
  never reaches another player. Enforced structurally (no `private_to`, never in
  `messages`, keyed per player at every read). A test must assert that player A's
  prompt never contains player B's thought text.

---

## 4. Testing Strategy

The reflection effect is LLM-driven (effort-not-results for the *effect*), but
the **structure** is fully testable with a fake LLM. Test intents (not full
bodies):

- **Reflection writes per surviving AI, skips dead + human.** Drive a Day round
  to its wrap with a `fake_large` scripting `Reflection` outputs; assert
  `private_thoughts` gains one entry for each surviving AI and **none** for the
  human or any dead player.
- **Accumulation reducer.** Unit-test `_merge_private_thoughts` directly: two
  successive deltas for the same player concatenate in order; a new player key is
  added without disturbing others; inputs are not mutated (purity).
- **Own-thoughts-only injection (privacy).** With two AI players each holding
  distinct prior thoughts, capture the rendered Day-speak / vote / pointing
  prompts (via the fake's `last_messages` / `messages_log`, as the spec-015
  prompt-threading tests do) and assert each prompt contains only the acting
  player's notes and **never** the other player's note text.
- **Flag-off parity (ADR 011, REQUIRED).** With `GRAPHIA_PRIVATE_THOUGHTS`
  falsy: (a) `day_round_reflect` writes nothing / is a no-op; (b) the three
  prompts are **byte-identical** to their pre-028 form (the `{private_thoughts}`
  slot collapses to `""`). This is the parity test ADR 011 requires for every
  gameplay flag.
- **`_private_thoughts_block` purity / shape.** Unit-test the builder: `""` when
  disabled; `""` (or the neutral line — open decision 3) when enabled-but-empty;
  the labelled block when enabled-with-notes; no RNG, no `set` iteration.
- **Eval-transcript rendering.** Feed a synthetic `events` log carrying a
  `private_thoughts` delta into `render_transcript`; assert each thought renders
  as a private/annotated element (`<thought player="…">…</thought>`) attributed
  to its author, inside the right `<round>`, and that it is absent from any
  public-message rendering path. Defensive cases: missing channel, empty list,
  unknown id — no raise.
- **`safe_llm` / `FakeLargeUnified` extension (REQUIRED).** Add the `Reflection`
  queue + `reflections=` kwarg; a regression test that a full-game run with the
  default-on flag does not raise the "no scripted queue" assertion (i.e. the
  fake covers the new call site).
- **Dual-mode byte-equal smoke is unaffected.** Re-run
  `test_dual_mode_smoke.py`. Because reflection emits no public `messages`, the
  `#public-log` compared by the test is unchanged. Two things to verify at
  implementation: (1) the new node's **own super-step** is identical across
  modes (it is — same graph, same RNG-free node body; the LLM is faked in tests
  but the node makes no RNG draw), and (2) the new `private_thoughts` channel is
  excluded from the public-log comparison (it is — the test scopes to
  `#public-log`, and private channels are explicitly out of scope per the test's
  own docstring). No new RNG is introduced, so the seeded trajectory is
  preserved. If the smoke test's full-game run reaches a Day round wrap, the
  `fake_large` it builds must also script `reflections=` (otherwise the
  loud-failure path fires) — fold this into the test's fake setup.
- **Structural invariants over textual equality** (architecture §6): assert "a
  thought was produced", "it fed the next prompt", "no cross-player leak" — never
  the verbatim thought text.

---

## 5. Open Decisions

1. **Router predicate vs. node self-guard for the round-wrap → reflect →
   day_turn loop.** The reflection node must fire on a completed-round loop-back
   but not on the round-cap close, vote, or re-prompt branches. Two viable
   shapes: (a) make `route_day_turn_or_vote` return `"day_round_reflect"` on the
   precise loop-back-and-not-close branch (router owns the decision), or (b)
   always route the loop-back through `day_round_reflect` and have the node
   self-guard on `day_turn_index == 0 and day_rounds >= 1`, no-op otherwise
   (node owns the decision). (a) keeps the node dumb but complicates the most
   delicate router in the codebase; (b) keeps the router minimally changed but
   spends a super-step on a no-op when conditions don't hold. Leaning (b) for
   router-stability, but this is a genuine call for the implementer.
2. **Slot placement of `{private_thoughts}` in each template.** Likely just
   before `{role_guidance}` (so the player's own reasoning precedes the closing
   nudge, and the spec-024 directive stays the last/most-salient thing read) —
   but recency arguments could put it last. Needs a deliberate choice consistent
   across all three templates.
3. **Enabled-but-empty block: `""` vs. a neutral "(no private notes yet)"
   line.** `""` keeps round-1 prompts byte-identical to pre-028 (cleaner
   ablation story); a neutral line is marginally more informative to the model.
   Leaning `""` for parity-cleanliness.
4. **`stream_mode="updates"` payload shape for a custom-reducer channel.** The
   eval-transcript renderer assumes each `private_thoughts` delta carries the
   node's *return* value (this step's new thoughts), not the post-reducer merged
   map. I could not fully confirm from the code alone whether LangGraph's
   `updates` stream emits the raw node return or the reduced value for a channel
   with a custom reducer; verify at implementation and, if it emits the merged
   map, have the renderer diff against the prior super-step instead of rendering
   the delta directly.
