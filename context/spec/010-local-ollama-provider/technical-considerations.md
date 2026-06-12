# Technical Specification: Local Ollama Provider

- **Functional Specification:** [`./functional-spec.md`](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Alexey Tigarev
- **Implements:** [ADR 009 — Pluggable LLM Provider Abstraction](../../adr/009-pluggable-llm-provider-abstraction.md) · [ADR 010 — Anthropic-Compatible Ollama Protocol](../../adr/010-anthropic-compatible-ollama-protocol.md)

---

## 1. High-Level Technical Approach

The whole game reaches the LLM through **one seam**: `get_large()` / `get_small()` in `src/graphia/llm.py`, called everywhere as `get_X().with_structured_output(<Schema>).invoke(messages)` (in `nodes/setup.py`, `nodes/night.py`, `nodes/day.py`). Per **ADR 009** the change introduces a small **provider abstraction** behind that seam, and per **ADR 010** the local provider speaks Ollama's **Anthropic-compatible** endpoint:

1. **A provider abstraction with two implementations** (ADR 009): an abstract `LLMProvider` exposing a heavyweight (`large`) and lightweight (`small`) tier, with a `BedrockProvider` (today's `ChatBedrockConverse`) and an `OllamaProvider`. `get_large()`/`get_small()` **branch on a provider setting** to pick the active implementation and cache the singletons as today. The call sites are **untouched** — both implementations return a structured-output-capable LangChain chat model.
2. **The Ollama implementation talks Anthropic Messages** (ADR 010): `OllamaProvider` returns a `ChatAnthropic` pointed at the local Ollama `/v1/messages` endpoint (`base_url`, dummy api-key, `anthropic-version` header), with the configured Ollama model per tier. Structured output is Anthropic **tool-use** over that endpoint.
3. **Config, fail-fast preflight, remote guard, quickstart** — as below.

The structured-output path is the **load-bearing risk** and is gated: ADR 010 requires a **smoke-test** that tool-use over `/v1/messages` reliably produces the flat schemas with the recommended models **before** relying on it; the ADR-009 abstraction makes the **fallback** (swap `OllamaProvider`'s client to native `ChatOllama` or an OpenAI-compatible client) a contained change. No game schema changes — `Roster` / `Pointing` / `Ballot` / `DayAction` are already flat (kept that way for Bedrock Converse), and a malformed/invalid local-model turn flows into the **existing** retry-then-fallback in the node helpers.

This keeps the two-tier pattern (architecture §4) — it adds a provider axis, not a third tier — and makes local mode **fully offline** for the first time. That shift is recorded in **ADR 009** and has been folded into `architecture.md` (§1/§3/§4, 2026-06-11).

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Provider abstraction + factory branch — `src/graphia/llm.py` — **[Agent: langgraph-agentic]**

- Introduce an abstract `LLMProvider` with two tier accessors (`large()` / `small()`), each returning a LangChain `BaseChatModel` that supports `.with_structured_output(<Schema>)`.
- `BedrockProvider` → today's `ChatBedrockConverse` per tier (Nova Pro / Lite, region, temperatures unchanged).
- `OllamaProvider` → `ChatAnthropic(base_url=<ollama url>, api_key="ollama", model=<ollama model>, temperature=…, max_tokens=…)` per tier (Anthropic requires `max_tokens`; set a sensible cap).
- `get_large()` / `get_small()` select the provider from config, build/cache the singleton, and return `provider.large()` / `provider.small()`. **No call-site changes.**
- Model ids / Ollama model names stay **operational** (module constants + config overrides), per architecture §4's "model identities are operational, not architectural pins."

### 2.2 Structured output over Anthropic-compat — and the smoke-test gate

- `with_structured_output(<Schema>)` on the `OllamaProvider`'s `ChatAnthropic` uses Anthropic **tool-use**, which Ollama's `/v1/messages` maps to the underlying model's tool calling. The flat Pydantic schemas map directly to tool input schemas.
- **Verify-at-implementation gate (ADR 010 §3):** before relying on it, smoke-test that tool-use / structured output over `/v1/messages` reliably yields valid `DayAction` / `Pointing` / `Ballot` / `Roster` with the recommended models. **Fallbacks if it underperforms:** native `ChatOllama` (`langchain-ollama`, JSON-schema `format`) or OpenAI-compat (`langchain-openai`, base_url) — swapped inside `OllamaProvider` only, thanks to the ADR-009 abstraction.
- **No new failure mode in the game loop:** `_ai_day_action`, `_ai_pick_target`, `_ai_ballot` already retry once then fall back deterministically — covering functional-spec §2.4 third criterion regardless of provider.

### 2.3 Config surface — `src/graphia/config.py` — **[Agent: python-backend]**

New env-driven fields on `GraphiaConfig` (parsed in `load_config`):

| Setting (env) | Purpose | Default |
| --- | --- | --- |
| `GRAPHIA_LLM_PROVIDER` | `bedrock` \| `ollama` | `bedrock` (existing behaviour unchanged) |
| `GRAPHIA_OLLAMA_BASE_URL` | Ollama base URL (the Anthropic client points here; it calls `/v1/messages`) | `http://localhost:11434` |
| `GRAPHIA_OLLAMA_LARGE_MODEL` | gameplay model | a documented recommended default (§2.6) |
| `GRAPHIA_OLLAMA_SMALL_MODEL` | mechanical (name-gen) model | a documented recommended default (§2.6) |

- Validate `GRAPHIA_LLM_PROVIDER` against the allowed set with a clear `SystemExit` on a typo (mirrors the `GRAPHIA_ROLE` validation).
- **Remote-mode contradiction guard:** `remote_mode` + provider `ollama` → clear `SystemExit` ("Ollama runs on your machine and can't be reached from the deployed Runtime; use the cloud provider for `--remote`, or drop `--remote` to play locally on Ollama"). Mirrors the existing remote-without-`GRAPHIA_RUNTIME_URL` check; enforces functional-spec §3 (Ollama = local only).

### 2.4 Fail-fast preflight — app boot — **[Agent: python-backend]**

- When provider `ollama`, run a cheap **preflight before the TUI starts** (the project's fail-fast-load-config-before-TUI posture): confirm the Ollama base URL is reachable and both configured models are installed (Ollama lists installed models via its native `/api/tags`).
- On failure, exit with a **plain-language message and no stack trace**:
  - unreachable → "Couldn't reach Ollama at `<url>`. Is it running? Start it with `ollama serve`."
  - missing model → "The model `<name>` isn't installed. Pull it with `ollama pull <name>`."
- Satisfies functional-spec §2.4 criteria 1–2. (Mid-game failures use §2.2's existing fallbacks, not the preflight.)

### 2.5 Dependency — `pyproject.toml` — **[Agent: python-backend]**

- `uv add langchain-anthropic` (provides `ChatAnthropic`; pulls the `anthropic` SDK transitively). Pin per project convention.
- The fallback clients named in §2.2 (`langchain-ollama`, `langchain-openai`) are **only** added if the smoke-test forces a switch — not part of the default plan. Ollama itself is the player's responsibility (functional-spec §3 out-of-scope).

### 2.6 Quickstart + recommended models — docs (`README.md`) — **[Agent: python-backend]**

- A "Play offline with Ollama" section: install Ollama, `ollama pull <recommended-large>` and `<recommended-small>`, set `GRAPHIA_LLM_PROVIDER=ollama` in `.env`, `make play`.
- **Recommended defaults (operational — confirm via the §2.2 smoke-test):** a **tool-capable** instruct model for the gameplay tier and a small tool-capable model for name-gen (candidates: `qwen3-coder` / `qwen2.5` / `llama3.1` for large; a 3B-class tool-capable model for small). Tool-use over `/v1/messages` needs models that support tools, so the recommendation is constrained to tool-capable models. Quality is explicitly not guaranteed (functional-spec §2.5).

---

## 3. Impact and Risk Analysis

- **System dependencies / blast radius:** confined to `llm.py` (the new abstraction + factory branch), `config.py` (4 fields + 2 guards), an app-boot preflight, one dependency, and docs. **No graph, node, state, UI, or schema change** — every call site already goes through `get_large`/`get_small`, and `safe_llm` patches those two functions *above* the provider branch, so the suite stays fully mocked/offline.
- **Architectural decisions:** recorded in **ADR 009** (provider abstraction) and **ADR 010** (Anthropic-compat protocol). The architecture doc's "LLM Client: `ChatBedrockConverse`" and "local mode hits AWS only for Bedrock" lines still need revising (`/awos:architecture`).
- **Risk — structured output over `/v1/messages` is unverified (the headline risk, per ADR 010).** Ollama's Anthropic-compat is its newest surface; tool-use reliability with local models is not yet confirmed. *Mitigation:* the §2.2 smoke-test gate **before** reliance, with native `ChatOllama` / OpenAI-compat as contained fallbacks (the ADR-009 abstraction is what makes the swap cheap), plus the existing retry-then-fallback in the loop.
- **Risk — model-dependent quality / behaviour divergence (ADR 009 §5).** Weak local models produce more invalid output → more fallbacks → blander play; the two implementations (Nova vs a local model) can behave very differently. *Mitigation:* recommend capable models; **evaluate** with the dialogue-diversity eval harness (spec 009); quality is non-guaranteed by the spec.
- **Risk — `langchain-anthropic` / Anthropic-version-header specifics over Ollama.** Confirm the `base_url`/header/`max_tokens` wiring against the installed versions at implementation; the `/v1/messages` endpoint itself is confirmed present.
- **Determinism posture unchanged (architecture §6).** Local-model output stays non-reproducible; no new seed protocol; mechanical RNG untouched.

---

## 4. Testing Strategy

- **Offline unit tests (the standard suite) — [Agent: testing]:**
  - Config: `GRAPHIA_LLM_PROVIDER` defaults to `bedrock`; `ollama` parses the model/url fields; an invalid provider value and the `remote + ollama` combination each raise a clear `SystemExit`.
  - Provider/factory: with provider `ollama`, `get_large()`/`get_small()` build an `OllamaProvider` whose tiers are `ChatAnthropic` configured at the local `base_url` + model (asserted by type / config) **without any network call**; with `bedrock`, unchanged.
  - Preflight: with a stubbed-unreachable Ollama and a stubbed missing-model, the boot path exits with the specific plain-language message and **no traceback**.
  - The existing `safe_llm` net is untouched and the full suite stays green (the branch is below the patched `get_large`/`get_small`).
- **Manual / `make`-gated real-Ollama smoke (outside `pytest`) — doubles as the ADR-010 structured-output gate:** with Ollama running on the recommended models, drive one full local game over `/v1/messages` and confirm structured output works end-to-end and the game completes offline. Mirrors the project's real-LLM-eval posture (`make eval-dialogue` etc.); not part of the mocked suite. If structured output is unreliable, exercise the §2.2 fallback before sign-off.
- No assertion depends on local-model text (architecture §6); behavioural checks remain structural.
