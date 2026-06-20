# Technical Specification: Fuller Multi-Day Discussion Window for AI Players

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

---

## 1. High-Level Technical Approach

A **local-LangGraph prompt-windowing change** plus a **provider-context investigation**. `_render_context` (in `nodes/day.py`) is the single function that renders the "recent discussion" shown to an AI player at both its speak turn (`_ai_day_action`) and its vote (`_ai_ballot`). Today it keeps the last `_CONTEXT_WINDOW = 30` speaker-visible messages — a module-global constant that doesn't even cover one full Day at the default table (~40–45 messages). This change makes the window a **configurable setting** (`GRAPHIA_CONTEXT_WINDOW`, default a generous value spanning 3+ days) and threads it like the other ADR-011 flags through **both** `build_graph` and `build_runtime_graph`.

The window enlargement is only safe if two things hold (the functional spec's R2/R3 crux):

- **R2 — headroom:** the window must fit the model's real working context with comfortable headroom, never filling it to the limit.
- **R3 — never silently drop the essentials:** the rendered history must never overflow the model's context such that the *oldest* tokens — which sit at the prompt top and include the player's own role, objective, and instructions — get silently truncated by the model server.

To guarantee R3 independent of operator configuration, the design adds a **defensive token-budget cap** on top of the message-count window: the rendered history self-trims to a token budget derived from a conservatively-assumed context size, so even if the model's effective context is *smaller* than assumed, the prompt cannot overflow — it just carries fewer history messages. The role grounding / persona / standings / role-guidance are assembled *outside* `_render_context` and are never subject to its trim.

A **provider-context verification** is the load-bearing risk. The local (Ollama) path is reached via `ChatAnthropic` against Ollama's Anthropic-compatible `/v1/messages` endpoint (ADR 010) with **no `num_ctx` set**. The verified finding (see §2 "Ollama context-length mechanism — verified") is that the Anthropic-compat endpoint **cannot** take a per-request context size, and Ollama's server default context is **small (4096 tokens, 2048 in older builds)** with **silent truncation** — so the assumed 32K is *not* what the model receives unless the operator sets it. **Decided (with the user): Route A** — keep `ChatAnthropic` and rely on the operator's server-side `OLLAMA_CONTEXT_LENGTH` (already set to 32K), backed by the app-side token-budget cap + a startup context check (see §2). Route B (switch the large tier to `ChatOllama(num_ctx=…)`) was rejected to avoid re-routing the `with_structured_output` gameplay path onto a different client family and re-validating it.

Acceptance follows **effort-not-results** (architecture §6 "Determinism Posture & Testing Conventions" / CR 005): the suite verifies the window default/override, the never-truncate-the-role invariant, and the token-budget cap; the behavioral effect is eval-measured out-of-suite.

This spec shares its edit surface with **spec 024 (Role-Specific Day Guidance)** — both touch `_ai_day_action` / `_ai_ballot`, `config.py`, and the graph threading. See §3 "Cross-spec coordination with spec 024" and spec 024's `technical-considerations.md` §3; **recommended order is 024 first, then 025**.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Windowing approach — three options, with a recommendation

The functional spec wants "at least roughly the last three days of events." Three ways to express that:

1. **Count window (today's mechanism, enlarged).** Keep `messages[-N:]` with a larger `N`. *Pro:* zero new logic, exactly today's behavior with a bigger number, trivially ablatable back to 30. *Con:* "3 days" is approximated by a message count, which drifts with table size and vote activity (a vote-heavy Day emits more messages than a quiet one).
2. **Day-scoped window.** Walk back N day-boundaries' worth of events. *Pro:* the cleanest literal match to "the last three days." *Con:* needs day-boundary detection in the message stream — there is no explicit day marker in `messages` today; it would key off the `day_open` victim-reveal / recap `SystemMessage`s (the `" status:"` recap marker, or the "Day breaks" line), which is brittle string-matching and couples the window to recap phrasing. New logic, new failure mode.
3. **Token-budget window.** Trim to a token budget rather than a message count. *Pro:* directly expresses "fit the model's context with headroom" (R2) and is the natural home for the R3 defensive bound. *Con:* needs a token estimate per message (a cheap heuristic, not a real tokenizer — see below); a budget alone doesn't express "3 days" in human terms.

**Recommendation: a configurable message count (option 1) as the primary control, with a defensive token-budget cap (option 3) layered on top.** The count is the human-legible "how many days" knob the functional spec and the ablation story want (default sized to ~3+ days; set back to 30 reproduces prior behavior exactly). The token-budget cap is the R3 safety net that makes overflow impossible regardless of the count or the operator's server config. Day-scoped (option 2) is the cleanest *conceptual* match but is rejected for v1: day-boundary detection couples the window to recap/announcement phrasing (a real bug class given specs 018/020/021 already churn that text), and the message-count default — sized against the measured ~40–45-messages-per-Day table — already delivers "3+ days" without parsing the stream. If the count proves too coarse, day-scoping is a clean follow-up.

### Component Breakdown

**`src/graphia/nodes/day.py` — `_render_context` becomes window-aware**

- `_render_context(messages, speaker_id, *, window: int, token_budget: int)` *(signature change)* — the privacy filter (drop whispers addressed to other players; keep public + own whispers), the label rules, and the visible-first-then-trim ordering are **unchanged**. Two changes:
  - the `[-_CONTEXT_WINDOW:]` slice uses the passed `window` instead of the module global;
  - after the count slice and line rendering, apply the **defensive token-budget cap**: estimate the token size of the rendered history and, if it exceeds `token_budget`, drop **oldest** lines until it fits. (Oldest-first because the most recent discussion is the highest-signal, and because the role/instructions are *not* in this string — they're assembled separately and protected.)
- **Token estimate:** a cheap, dependency-free heuristic (e.g. `len(text) / 4` chars-per-token, or whitespace-token count × a fudge factor) — **not** a real model tokenizer. The cap is a safety bound, so a conservative over-estimate is the safe direction (it trims *earlier*, never later). *(Verify-at-implementation: confirm the heuristic over-estimates rather than under-estimates for the game's short English speeches; document the chosen ratio.)*
- Keep `_CONTEXT_WINDOW = 30` as the **documented prior-behavior baseline constant** (referenced by the existing `tests/test_slice_day_context_window.py` guards and re-usable as the ablation value), but it stops being the live window — the live value comes from config.
- **`_ai_day_action` / `_ai_ballot`** *(call sites)* — each gains keyword-only params `context_window: int` and `context_token_budget: int` (defaulted to the prior baseline / a generous budget so direct test calls stay valid), and forwards them into the `_render_context(...)` call. Both already build `context` from `state["messages"]`.

**`src/graphia/config.py` — the window setting (+ optional budget)**

- New field `context_window: int = <default>` on `GraphiaConfig` (defaulted, like the other spec fields). The default is a generous count sized to ~3+ days: at ~40–45 messages/Day, **~150** covers three-plus days with margin. *(Confirm the per-Day message count at implementation by reading a recorded transcript under `evals/transcripts/`.)*
- Parse in `load_config` via the existing `_parse_count("GRAPHIA_CONTEXT_WINDOW", <default>)` (same helper `GRAPHIA_NUM_CITIZENS` / `GRAPHIA_MAX_DAYS` use); reject `< 1` with a named `SystemExit` (a zero/negative window is nonsensical), matching the `max_days` guard.
- **Token budget:** recommended as a **derived constant**, not a separate env var, to keep the operator surface small — derive it from a conservatively-assumed context size minus a reservation for the fixed prompt scaffold (system prompt + role grounding + persona + standings + role-guidance + the model's `max_tokens` completion reserve), then apply a headroom fraction (R2). If the assumed context size needs to track the provider/model, expose `GRAPHIA_CONTEXT_TOKEN_BUDGET` as an optional override; otherwise a module constant is enough. *(Finalized: a derived constant; expose `GRAPHIA_CONTEXT_TOKEN_BUDGET` only if a future provider needs a different assumed context.)*

**`src/graphia/graph.py` and `src/graphia/runtime/graph_builder.py` — threading (anti-drift)**

- `_assemble_graph` gains `context_window: int` (and the token budget if it becomes config-driven), bound via `partial(...)` into the two AI-decision nodes **alongside** `recap_aware_reasoning_enabled` (and spec 024's `role_guidance_enabled`): into `day_turn` (→ `_ai_day_action`) and into `collect_votes` (→ `_ai_ballot`). `day_turn` / `collect_votes` forward the params down.
- `build_graph` passes `context_window=config.context_window`.
- `build_runtime_graph` gains `context_window: int = <default>` (defaulted to the config default) and forwards it to `_assemble_graph`; the Runtime entrypoint passes `load_config().context_window`. **Both** builders must thread it (the named anti-drift requirement — the `graph_builder.py` docstring's prior-incident note).

No `state.py` change; no new node; no new LLM call site; `safe_llm` needs no extension.

### Ollama context-length mechanism — verified

Findings (verified against the Ollama docs + the LangChain reference; the few items not fully confirmable are flagged for implementation):

- **Ollama's server default context is small and truncates silently.** Default `num_ctx` is **4096 tokens** (current docs; **2048** in some/older builds) and Ollama **silently clips** any input exceeding it — the truncated (oldest) tokens never reach the model, no error raised. *This is the R3 failure mode made concrete:* on the default local-Ollama config, a prompt longer than ~4K tokens silently loses its top — which is where the player's role/objective/instructions live. (This already threatens the *current* 30-message window on a long Day; spec 025 makes it acute.)
- **The model's own context is not the constraint.** `qwen3-coder:30b` (the project's recommended Ollama large tier) has a **native 256K-token context** (extendable further with YaRN); the user's "~32K" figure is a practical OOM-avoidance recommendation, not the ceiling. So the binding constraint is the **Ollama server `num_ctx`**, not the model.
- **The Anthropic-compatible `/v1/messages` endpoint cannot set context size per request.** Its accepted fields are the standard Anthropic Messages set — `model`, `max_tokens`, `messages`, `system`, `stream`, `temperature`, `top_p`, `top_k`, `stop_sequences`, `tools`, `thinking`. There is **no `num_ctx` and no `options` field.** So **`ChatAnthropic` cannot raise the Ollama context window from application code** on the current path — full stop. `num_ctx` is an Ollama-*native* option (carried in the `options` object on `/api/chat` and the OpenAI-compat `/v1/chat/completions`), not an Anthropic parameter.
- **On the current `ChatAnthropic` + `/v1/messages` path, the only lever is server-side `OLLAMA_CONTEXT_LENGTH`** (e.g. `OLLAMA_CONTEXT_LENGTH=32768 ollama serve`), which sets the server's default context for all endpoints. That is **operator configuration, outside the app's control** — the app cannot guarantee it, only document it and defend against its absence (the token-budget cap).
- **`langchain-ollama` `ChatOllama` takes `num_ctx` as a direct constructor parameter** (confirmed in the LangChain reference) and sends it through Ollama's native API — so switching the Ollama large tier to `ChatOllama` would let **application code set the context window** deterministically, removing the operator dependency.

**Approach (two parts):**

1. **App-side R3 defense regardless of provider:** ship the **defensive token-budget cap** in `_render_context`, sized to a conservatively-assumed effective context so the prompt can't overflow even an unconfigured server. This satisfies R3 "trim to fit rather than overflow" with no provider change and protects the Bedrock path too. The cap is the safety net; the count window (~150) is the normal limiter.

2. **Decision (confirmed with the user): Route A — keep `ChatAnthropic`, deliver the window via server-side `OLLAMA_CONTEXT_LENGTH`.** The operator has **already set the Ollama server context to 32K** (`OLLAMA_CONTEXT_LENGTH=32768`), which Ollama applies to every loaded model including the Anthropic-compatible `/v1/messages` endpoint — so `ChatAnthropic` receives the full 32K with **no provider-code change** and **ADR 010 stays intact** (single Anthropic client). At the ~150-message / 3+-day design a worst-case (Mafioso) Day prompt is **~6–8K tokens** (≈0.7–0.9K fixed scaffold + ~4.5–6K history + ~1K completion reserve) — only **~20–25% of 32K**, ~24K headroom, deliberately well short of filling the context (R2, anti-dilution).

   **Route B rejected** (switch the large tier to `ChatOllama(num_ctx=…)`): more app-enforced, but it amends ADR 010 and — the decisive con — re-routes the tier that **all gameplay drives through `with_structured_output`** (DayAction / Ballot / Roster) onto a different client family, risking the structured-output reliability ADR 010 chose the Anthropic-compat surface for. Not worth that risk for a context-window tuning change when the server is already at 32K.

   **Belt-and-braces for the route-A footgun** (a fresh/forgetful server reverting to ~4K): add a lightweight **startup context check** — query the loaded model's effective context (Ollama `/api/show` / `/api/ps`) and **log a warning** if it is below the configured window's token budget — plus document `OLLAMA_CONTEXT_LENGTH=32768` in `.env.example` / README / `make ollama-smoke`. Because the token-budget cap makes overflow impossible, this is at worst a "fuller window not delivered" warning, never a truncated-instructions failure. Nova (Bedrock) is unaffected — its context far exceeds this window (verify exact Nova Pro/Lite size at implementation if cheap; not the binding constraint).

### Measurement (operational, not code)

After the change, run `make blunder-eval` and compare against the recorded baseline in `evals/blunder-ledger.yaml`: **repetition** (the spec's primary hypothesis — a fuller window lets a player see and avoid re-treading old points), win-rate by side, votes initiated, share resolved vs `no_winner`. Log confirmed or refuted (both satisfy acceptance, per CR 005). Run per provider for comparable records; clean-or-commit transcripts before the measured run.

---

## 3. Impact and Risk Analysis

- **System dependencies:** the AI-turn invokes and the ADR-011 flag pattern (no new ones). The **only** external dependency that changes is the local Ollama provider's context configuration (route A or B above) — Bedrock is unaffected.

- **Potential risks & mitigations:**
  - **Silent truncation (R3) — the load-bearing risk.** Mitigated app-side by the token-budget cap (overflow becomes impossible); mitigated provider-side by route A or B. The cap protects even an unconfigured Ollama server.
  - **Window not actually delivered on local Ollama.** The server is already at 32K (Route A); the residual risk is a fresh/forgetful server reverting to ~4K. The **startup context check** logs a warning when the effective context is below the window's budget, and the token-budget cap prevents *harm* — so the fuller window simply isn't *delivered* (a measurable "no effect" in the eval), never a truncated-instructions failure. Mitigation: the startup warning + prominent `.env.example`/README/`make ollama-smoke` documentation of `OLLAMA_CONTEXT_LENGTH=32768`.
  - **Dilution (R2).** Too large a window buries signal in stale chatter. Mitigated by sizing the default to ~3+ days with headroom (not the whole game) and keeping the budget cap below full context — the functional spec's "bounded window, not the entire game."
  - **Token-estimate accuracy.** The heuristic is deliberately approximate; the safe direction is over-estimation (trims earlier). Mitigated by choosing a conservative chars-per-token ratio and a synthetic-oversized-history test.
  - **Cost/latency on Bedrock.** A larger prompt means more input tokens per AI turn (every speak + every ballot). Bounded by the count default and budget cap; flag the per-game token-cost increase to the user, since `blunder-eval`'s Bedrock path pays for it.

- **Existing-test impact:** `tests/test_slice_day_context_window.py` imports `_CONTEXT_WINDOW` and `_render_context` directly and asserts the window holds a full round (`>= _MAX_TABLE_SIZE + 1`). Keeping `_CONTEXT_WINDOW = 30` as a baseline constant keeps those guards meaningful; the `_render_context` signature change (new keyword params) must be reflected in that file's direct calls and in `tests/test_day_context_privacy.py`. Enumerate callers of `_render_context` at implementation.

- **Cross-spec coordination with spec 024 (shared edit surface):** both edit `_ai_day_action` / `_ai_ballot` signatures, `config.py` (`GraphiaConfig` + `load_config`), and the `_assemble_graph` / `build_graph` / `build_runtime_graph` threading. They are **additive to different parts**: 024 appends a tail `{role_guidance}` slot + a `role`-keyed builder + the `GRAPHIA_ROLE_GUIDANCE` flag; 025 changes the windowing *inside* `_render_context` and adds the `GRAPHIA_CONTEXT_WINDOW` setting + token-budget cap + (possibly) the provider-tier change. **Recommended order: 024 first, then 025.** 024 is a smaller pure prompt + flag change with no provider question; landing it first means 025 re-touches the same template `.format()` test sites and threading params once, appending its window params next to 024's `role_guidance_enabled`. The new keyword params append to the partials independently — no conflict in this order. **Prompt-budget interaction:** with both landed, the token-budget cap trims only the *discussion history* inside `_render_context`; the spec-024 tail guidance, the spec-013 role grounding, the persona, and the spec-019 standings are assembled outside `_render_context` and are never trimmed (the R3 essentials guarantee). Confirm no double-trim.

- **Determinism:** the windowing is pure (a slice + a deterministic byte/char-based budget trim — no RNG, no `set` iteration, no wall-clock), so the byte-equal dual-mode smoke and seeded fakes are unaffected. The token-estimate heuristic must itself be deterministic (a pure function of the text).

---

## 4. Testing Strategy

Structural-invariant only (architecture §6 "Determinism Posture & Testing Conventions"); never asserts verbatim LLM text; behavioral effect is eval-measured out-of-suite. Extend or sit beside `tests/test_slice_day_context_window.py`.

- **Default window value + override:** `load_config()` yields the new generous default when `GRAPHIA_CONTEXT_WINDOW` is unset; an explicit value overrides it; `< 1` raises the named `SystemExit` (mirrors the `GRAPHIA_MAX_DAYS` config unit).
- **Window back to 30 reproduces prior behavior:** with the window set to `_CONTEXT_WINDOW` (30) and a synthetic message list, `_render_context` renders byte-identically to the pre-025 output (the ablation-parity test — set-it-back-to-30 reproduces the old short window).
- **Fuller window spans 3+ days:** build a synthetic multi-day message history (>3 days of speeches + recaps) and assert events from the earliest of those days survive into the rendered context at the default window (the R1 "reaches back across multiple days" check), where they would have been trimmed at window=30.
- **Role/grounding survives a very long history (R3, the load-bearing test):** drive the REAL `_ai_day_action` / `_ai_ballot` through a content-recording fake with a synthetic history far larger than any window/budget; assert the captured prompt still contains the actor's role label, win condition, persona, standings, and (post-024) the role-guidance — i.e. the essentials assembled outside `_render_context` are never trimmed away no matter how long the discussion. (Belt-and-suspenders: also assert at window=30 and at the fuller default.)
- **Token-budget cap trims oldest when oversized:** feed `_render_context` a history whose rendered size exceeds the token budget and assert the result fits the budget (by the heuristic), the **newest** lines are retained, and the **oldest** are dropped — never the (separately-assembled) role/instructions. Assert the cap is what trimmed (not the count window) by using a count window larger than the budget allows.
- **Token-estimate determinism:** the estimate is a pure function of text (same input ⇒ same number); no RNG, no clock.
- **Threading anti-drift:** `build_runtime_graph` carries `context_window` and forwards it to `_assemble_graph` (matching the spec-018/019/023 anti-drift coverage) — a config-off/on parity at the builder boundary or a signature-forwarding assertion.
- **Privacy unchanged:** the existing `tests/test_day_context_privacy.py` whisper-filtering invariants still hold under the new signature (other players' whispers never enter `speaker_id`'s context; the speaker's own whisper is kept and labelled) — re-run after the signature change.
- **No new fixtures / RNG; `safe_llm` needs no extension** (no new call site). Provider-context behavior (Ollama `num_ctx` / `OLLAMA_CONTEXT_LENGTH`) is **not** unit-tested against a real server — real Ollama/Bedrock are out of the mocked suite (architecture §6); it is verified manually via `make ollama-smoke` and recorded as the ADR-010 smoke gate if route B is chosen.
