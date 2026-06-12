---
spec: 010-local-ollama-provider
spec_title: Local Ollama Provider
introduced_on: 2026-06-12
---

# Concepts introduced in this increment

## Provider architecture

- **Provider strategy behind stable tier factories** (`llm-provider-strategy-two-tier`) — An abstract `LLMProvider` (with `large()`/`small()` tier accessors) and two concrete implementations, selected from config at the single factory seam every call site already passes through — so a whole second LLM backend lands with zero call-site changes.
- **Abstraction lands as a zero-behavior refactor** (`zero-behavior-refactor-slice`) — The first slice ships the abstraction with *only* the old provider behind it, proven safe by the full existing suite passing with zero test edits; every later slice then builds on an already-verified seam.
- **Driving one server through another vendor's protocol** (`compat-endpoint-client-reuse`) — Ollama serves Anthropic's Messages API at `/v1/messages`, so a stock `ChatAnthropic` client with a local `base_url` and a dummy api-key drives a local open model — chosen over the native and OpenAI-compatible surfaces for strategic client-unification reasons (ADR 010).
- **Offline by construction via a config gate** (`provider-conditioned-config-gate`) — When the local provider is selected, the config loader blanks every cloud-store id at the choke point, so the env-presence-gated store factories downstream *cannot* reach the cloud; the offline guarantee holds by construction, not by documentation.

## Reliability gating

- **Gate a transport on structured-output reliability** (`structured-output-capability-gate`) — A strategically-preferred but unproven transport is adopted only behind an explicit smoke-test gate with named fallbacks; if the gate fails, switching transports is a recorded decision, never a silent code change.
- **Count raw outcomes underneath the retry masking** (`instrument-under-the-fallbacks`) — The smoke harness wraps each tier client's `with_structured_output` in a counting proxy, so raw parse failures that the game's retry-then-fallback would mask are still measured — without it, a 100%-failure model still "completes" a game and looks fine.
- **Forced tool-choice is only as good as the model** (`tool-capable-model-requirement`) — The compat layer delivered tool calls for simple schemas, but a weak model answered conversational prompts in prose instead of calling the tool (40/40 failures); the recommendation must be a smoke-verified, tool-tuned model pair, not a size-based guess.

## Boot UX

- **Fail-fast preflight against a local service** (`fail-fast-preflight-against-local-service`) — Before the TUI starts, a cheap stdlib HTTP check confirms the local server is reachable and the configured models are installed, exiting with plain-language fix-it commands (`ollama serve`, `ollama pull <name>`) instead of a stack trace.
