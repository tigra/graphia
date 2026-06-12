# Tutorial 010: Local Ollama Provider — a second LLM backend, and the gate that proved it

- **Spec:** [`context/spec/010-local-ollama-provider/`](../../spec/010-local-ollama-provider/)
- **Status:** Draft
- **Author:** Alexey Tigarev
- **Date:** 2026-06-12
- **Prerequisites:** `001-playable-skeleton` (the structured-output schemas and the retry-then-fallback the whole increment leans on) and `009-ai-collusion-awareness` (the make-gated real-LLM eval posture the smoke harness extends). Helpful: `005-play-as-role` (fail-fast-before-TUI) and the ADRs this implements — [009](../../adr/009-pluggable-llm-provider-abstraction.md), [010](../../adr/010-anthropic-compatible-ollama-protocol.md).

---

## Overview

This increment gives Graphia a **second LLM backend**: a model served locally by [Ollama](https://ollama.com), so a full game runs **offline, at zero cloud cost** — and it does so without touching a single gameplay call site. The interesting design problem is the one every multi-provider LLM app eventually faces: *how do you add a provider when your whole codebase calls one vendor's client — and how do you trust a new transport whose structured-output behavior you've never seen?*

Two technologies answer it. The first is the classic **strategy pattern applied at an LLM client seam** — an abstract provider with two implementations, selected by config at the one factory every call site already passes through. The second is less obvious and more transferable: **Ollama's API-compatibility surfaces** — a local server that speaks *other vendors'* wire protocols (here, Anthropic's Messages API), letting a stock cloud client drive a local open model by changing nothing but the base URL.

The tutorial teaches core-outward: the seam itself; the discipline of landing it as a zero-behavior refactor; the borrowed protocol; then the part an LLM-integration reader should steal — **gating the unproven transport on measured structured-output reliability**, including how to instrument *underneath* your own retry/fallback safety nets so they can't hide the failures from you. The offline config gate and the boot preflight close it out.

---

## Concepts already covered (referenced, not re-taught)

- **`structured-output-flat-pydantic`** — every AI decision is a flat Pydantic schema (`Roster`/`Pointing`/`Ballot`/`DayAction`) via `with_structured_output`. 010's entire reliability question is whether a *new transport* can deliver these. (See [tutorial 001](../001-playable-skeleton/tutorial.md#bringing-in-the-llm-structured-output-and-self-correction).)
- **`chatbedrockconverse-singleton`** — the lazily-built tier clients behind `get_large()`/`get_small()`. 010 generalizes who *constructs* them. (See [tutorial 001](../001-playable-skeleton/tutorial.md#bringing-in-the-llm-structured-output-and-self-correction).)
- **`validation-retry-once-with-feedback`** — the node helpers retry a bad structured output once, then fall back deterministically. 010 measures *under* this net (§4) and relies on it for mid-game grace (§5). (See [tutorial 001](../001-playable-skeleton/tutorial.md#bringing-in-the-llm-structured-output-and-self-correction).)
- **`real-llm-eval-make-gated`** — harnesses that deliberately hit a real model live behind `make`, outside the mocked suite. 010's `make ollama-smoke` is the third such tool. (See [tutorial 009](../009-ai-collusion-awareness/tutorial.md#2-the-vehicle-a-real-model-eval-that-opts-out-of-the-mocked-suite).)
- **`in-process-factor-injection`** — applying experiment config via `setattr` on module seams instead of source edits. The smoke installs its counting proxies through exactly the seams 009 established. (See [tutorial 009](../009-ai-collusion-awareness/tutorial.md#6-running-the-conditions-without-touching-source).)
- **`fail-fast-load-config-before-tui`** — config errors exit with a clean message before any UI renders; 010's preflight extends the same posture to an external local service. (See [tutorial 005](../005-play-as-role/tutorial.md).)
- **`makefile-as-task-runner`** — operational entry points live behind `make` targets; `ollama-smoke` joins them. (See [tutorial 002 (v2)](../002-hosted-agentcore-deployment-v2/tutorial.md).)

---

## What's new this increment

- [**Provider strategy behind stable tier factories**](#1-the-seam-one-factory-two-providers) — a whole second backend behind the one seam, zero call-site changes.
- [**Abstraction lands as a zero-behavior refactor**](#2-landing-it-without-moving-anything) — ship the seam first, alone, proven by the untouched suite.
- [**Driving one server through another vendor's protocol**](#3-borrowed-protocol-an-anthropic-client-talking-to-ollama) — `ChatAnthropic` + local `base_url` = local open model.
- [**Gate a transport on structured-output reliability**](#4-prove-the-transport-the-structured-output-gate) — adopt the strategic bet only behind a smoke with named fallbacks.
- [**Count raw outcomes underneath the retry masking**](#4-prove-the-transport-the-structured-output-gate) — your safety nets hide failures; instrument below them.
- [**Forced tool-choice is only as good as the model**](#5-what-the-gate-caught-twice) — a weak model answers in prose; recommend verified pairs, not sizes.
- [**Offline by construction via a config gate**](#5-what-the-gate-caught-twice) — blank the cloud ids at the config choke point; the guarantee stops depending on the user's `.env`.
- [**Fail-fast preflight against a local service**](#6-for-completeness-greeting-the-first-run-user) — "Is Ollama even running?" answered before the TUI, with fix-it commands.

---

## Diagram

The seam, the two providers, and where the gate sits:

```mermaid
flowchart TD
    NODES["nodes/setup·night·day<br/>get_X().with_structured_output(Schema).invoke()"]
    NODES --> FACT["get_large() / get_small()<br/>(module-level cache, unchanged names)"]
    FACT --> RESOLVE["_resolve_provider()<br/>← GRAPHIA_LLM_PROVIDER"]
    RESOLVE -->|bedrock (default)| BP["BedrockProvider<br/>ChatBedrockConverse → Nova (AWS)"]
    RESOLVE -->|ollama| OP["OllamaProvider<br/>ChatAnthropic → http://localhost:11434<br/>(Anthropic-compat /v1/messages)"]
    OP -.->|"adopted only after"| GATE["make ollama-smoke<br/>per-schema raw tool-call counts<br/>qwen2.5:7b ✗ 40/40 · qwen3-coder:30b ✓ 0"]
    CFG["config offline gate:<br/>ollama ⇒ cloud store ids = None"] -.-> OP
```

---

## Walkthrough

### 1. The seam: one factory, two providers

**Pose.** Every AI decision in the game flows through one vendor's client. Now a second backend must exist — selected per run — without rewriting the four node modules that call the LLM. Where does the second provider *go*?

**Present.** The classic answer is the **strategy pattern, applied at the narrowest seam you already have**. Graphia's seam was built in tutorial 001: every call site does `get_large().with_structured_output(Schema).invoke(...)` — nobody constructs a client themselves. So the increment introduces a **provider strategy behind stable tier factories**: an abstract `LLMProvider` whose only job is *constructing* the two tier clients, with the factories owning selection and caching.

```python
# src/graphia/llm.py — LLMProvider / _resolve_provider
class LLMProvider(ABC):
    @abstractmethod
    def large(self) -> BaseChatModel: ...
    @abstractmethod
    def small(self) -> BaseChatModel: ...

def _resolve_provider() -> LLMProvider:
    match load_config().llm_provider:        # GRAPHIA_LLM_PROVIDER, default "bedrock"
        case "bedrock": ...                  # → BedrockProvider()
        case "ollama":  ...                  # → OllamaProvider()
```

**Apply.** Three deliberate boundaries make this clean. The interface returns LangChain's `BaseChatModel` — the *only* contract the call sites use is `.with_structured_output(...)`, so any provider that honors it slots in. Caching stays at module level (the **singleton tier clients** from tutorial 001, generalized), *not* inside the provider — which preserves the `llm._large` override seam that tutorial 009's **in-process factor injection** depends on. And it's deliberately a two-implementation abstraction with a `match`, not a plugin registry: per ADR 009, two providers want exactly that much machinery, no more. The transferable lesson: if your app already funnels LLM construction through one function, adding a provider is a *strategy object*, not a refactor.

### 2. Landing it without moving anything

**Pose.** The abstraction touches the most load-bearing file in the codebase — every AI call constructs through it. How do you land that without betting the whole increment on one big diff?

**Present.** As a **zero-behavior refactor slice**: the first slice ships the `LLMProvider` interface and `BedrockProvider` *only* — no config, no Ollama, no new behavior — and its definition of done is the **entire existing suite passing with zero test edits**. The unchanged suite *is* the proof that nothing observable moved.

**Apply.** This works because the public names never changed: `get_large`/`get_small` keep their signatures, so the autouse `safe_llm` net and every fixture patch-target in ~20 test files stay valid untouched. Each later slice (config, the Ollama provider, the preflight) then builds on a seam that's already verified in production shape. The sequencing discipline compounds: when the smoke later failed (§5), nobody had to wonder whether the *abstraction* was the problem — it had been proven inert before Ollama ever entered the picture.

### 3. Borrowed protocol: an Anthropic client talking to Ollama

**Pose.** The new provider needs a client for Ollama. The obvious choice is the native one (`langchain-ollama`). Why does Graphia instead point **Anthropic's** client at a local server?

**Present.** Because Ollama ships **API-compatibility surfaces**: besides its native API, the same local server speaks OpenAI's `/v1/chat/completions` *and* Anthropic's Messages protocol at `/v1/messages`. That makes **driving one server through another vendor's protocol** a real option — a stock `ChatAnthropic` works against a local open model with nothing changed but the base URL and a required-but-ignored api key:

```python
# src/graphia/llm.py — OllamaProvider.large
return ChatAnthropic(
    model=config.ollama_large_model,        # e.g. "qwen3-coder:30b"
    base_url=config.ollama_base_url,        # http://localhost:11434 → /v1/messages
    api_key=_OLLAMA_DUMMY_API_KEY,          # "ollama" — required but ignored
    temperature=0.7,
    max_tokens=_OLLAMA_MAX_TOKENS,          # Anthropic Messages mandates max_tokens
)
```

**Apply.** The *reason* is strategic, recorded in ADR 010: Anthropic Claude was this project's original direction, and Nova-on-Bedrock was a cost detour — so standardizing the local path on the Anthropic Messages surface keeps a door open to **one client family across cloud and local** if the detour ever reverses. The honest trade-offs went into the ADR too: it's Ollama's newest, least-proven compat layer, and whether **structured output** (Anthropic tool-use, which `with_structured_output` rides on) actually works over it was *unknown*. Two implementation details worth stealing: Anthropic's protocol requires an explicit `max_tokens` on every request (pick a cap that fits your turn shape), and temperatures mirror the Bedrock tiers so gameplay tone is provider-independent.

### 4. Prove the transport: the structured-output gate

**Pose.** The whole game runs on `with_structured_output` over flat Pydantic schemas (tutorial 001). If the compat endpoint quietly fails to deliver tool calls, the game doesn't crash — it degrades into canned fallback lines. How do you *adopt* a transport like that responsibly?

**Present.** You **gate the transport on structured-output reliability**: ADR 010 commits to the Anthropic-compat path *only* behind an explicit verify-at-implementation smoke — `make ollama-smoke` — with named fallbacks (native `ChatOllama`, OpenAI-compat) and the rule that switching is a *recorded decision*, never a silent swap. The harness drives one real scripted game per candidate model pair and reports a per-schema verdict table.

But there's a trap, and it's the most transferable lesson in this increment. The game's own **retry-once-then-fallback** (tutorial 001) *masks* structured-output failures by design — a model that never returns a valid `DayAction` still "completes" a game, every turn quietly replaced by `"I'm not sure who to trust yet."`. So the harness must **count raw outcomes underneath the retry masking**: a thin proxy wraps each tier client's `with_structured_output` and records every raw invoke result *before* the game's safety nets see it.

```python
# src/graphia/tools/ollama_smoke.py — _CountingModel / _CountingStructured
class _CountingModel:
    """Thin proxy over a tier client: intercepts with_structured_output
    and delegates everything else untouched."""
    def with_structured_output(self, schema, **kwargs):
        return _CountingStructured(
            self._inner.with_structured_output(schema, **kwargs), schema, self._stats
        )   # ._invoke records (schema, success | exception | non-instance) raw
```

**Apply.** The proxies are installed through the same module seams as tutorial 009's **in-process factor injection** (`llm._active_provider`, `llm._large`/`_small`) — no production edits — and the whole harness follows 009's **make-gated real-LLM eval** posture: real model, real game, deliberately outside the mocked `pytest` suite. Composition pays off: three increments of test-seam discipline made a transport-reliability gate a ~one-file tool.

### 5. What the gate caught, twice

**Pose.** So the gate ran. What did it actually catch — and was it worth the ceremony?

**Present — first firing: the environment, not the model.** The first live run died 2.5 seconds in with an *AWS SSO error* — in a game that was supposed to be offline. The cause: the career-stats and diary store factories gate on **env-var presence** (`career_memory_id` set ⇒ AgentCore Memory), so a `.env` wired to a deployed stack had even local games emitting events to the cloud — a violation of the spec's "no cloud service" promise that only surfaced because the SSO token happened to be expired. The fix is **offline by construction via a config gate**: when the provider is `ollama`, the config loader blanks every cloud-store id at the single choke point, so the downstream factories *can't* pick their cloud implementations:

```python
# src/graphia/config.py — load_config (offline gate)
if llm_provider == "ollama":
    memory_id = None
    career_memory_id = None
    gateway_id = None
    gateway_url = None
    stats_strategy_id = None
```

The guarantee moved from *documentation* ("unset these vars for offline play") to *construction* — a fresh contributor's machine and a fully-wired developer machine now behave identically under `ollama`.

**Present — second firing: the model, not the transport.** Env-isolated, the gate then rejected the planned default. `qwen2.5:7b` produced **40/40 `DayAction` failures** — every one a `None`, meaning the model *answered in prose instead of calling the tool* — while the same run's simple schemas (`Roster`, `Pointing`) passed. That's the fingerprint of **forced tool-choice being only as good as the model**: the compat layer transported tool calls fine; a mid-size general model just ignores the tool when the prompt feels conversational. The tool-tuned `qwen3-coder:30b` then verified clean — 0 failures across every schema, with `Ballot` (never exercised by the scripted game) confirmed later by a real vote in live play. The verified pair became the config defaults; the README recommends *smoke-verified models*, not parameter counts, and points candidate-curious users at `make ollama-smoke ARGS="--models <large>,<small>"`.

**Apply.** Note what *didn't* happen: ADR 010's fallback clause was never invoked — the Anthropic-compat bet held once a capable model was in place. And both firings were things no mocked test could see: one was the user's environment, one was a real model's behavior. The gate earned its ceremony twice before lunch.

### 6. For completeness: greeting the first-run user

**Pose.** The first thing a new offline player hits isn't a model-quality problem — it's "is Ollama even running, and did I pull the models?" What does that failure look like?

**Present.** A **fail-fast preflight against a local service**: before the TUI starts (the same boot posture as tutorial 005's fail-fast config loading), a stdlib-only HTTP check hits Ollama's `/api/tags` with a 3-second timeout, verifies both configured models are installed, and exits with fix-it commands — `Couldn't reach Ollama at <url>. Is it running? Start it with: ollama serve`, and one `ollama pull <name>` line per missing model (all reported at once, so the player doesn't fix one and trip on the next). No stack trace; the TUI never renders.

**Apply.** Mid-game failures deliberately stay out of the preflight's scope — those belong to the **retry-then-fallback** net from tutorial 001, which §4's gate verified behaves sanely under a local model. Boot problems get plain language; runtime problems get graceful degradation; the two mechanisms never overlap.

---

## Try it

With [Ollama](https://ollama.com) installed:

```
ollama pull qwen3-coder:30b   # gameplay model (~19 GB)
ollama pull qwen2.5:3b        # name-generation model
# in .env:  GRAPHIA_LLM_PROVIDER=ollama
make play                     # a full game, no AWS, no internet
```

To see the gate in action, run `make ollama-smoke` (the verified defaults pass with a 0-failure table) — then try `make ollama-smoke ARGS="--models qwen2.5:7b,qwen2.5:3b"` and watch the rejected model's verdict: `DayAction 40/40 failures … UNRELIABLE`, while the game still "completes" on canned fallbacks. The failure-UX is one `pkill ollama` away: `make play` then exits in under a second with the `ollama serve` hint.

---

## Where to go next

- **ADRs this implements:** [009 — Pluggable LLM Provider Abstraction](../../adr/009-pluggable-llm-provider-abstraction.md) and [010 — Anthropic-Compatible Ollama Protocol](../../adr/010-anthropic-compatible-ollama-protocol.md) (the alternatives and the strategic rationale, in full).
- **Previous tutorial:** [009 — AI Collusion Awareness](../009-ai-collusion-awareness/tutorial.md) — the real-LLM eval posture and injection seams this increment's gate is built from.
- **Foundations:** the structured-output schemas and retry-fallback in [tutorial 001](../001-playable-skeleton/tutorial.md), and the fail-fast boot posture in [tutorial 005](../005-play-as-role/tutorial.md).
