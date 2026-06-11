# ADR 010: Anthropic-Compatible Protocol for the Local Ollama Provider

- **ADR Number:** 010
- **Title:** Anthropic-Compatible Protocol for the Local Ollama Provider
- **Status:** Accepted
- **Date:** 2026-06-11
- **Authors:** Alexey Tigarev

---

## 1. Context

ADR 009 established a pluggable LLM-provider abstraction (Bedrock + a local **Ollama** implementation, selected by a factory branch) but **deferred** which protocol/client the Ollama implementation speaks to reach local models. That is this decision.

Ollama exposes three usable client surfaces: its **native API** (`ChatOllama` via `langchain-ollama`), an **OpenAI-compatible** endpoint (`/v1/chat/completions`), and an **Anthropic Messages–compatible** endpoint (`/v1/messages`, confirmed against the Ollama docs). The game depends on **structured output** for every AI decision (the flat Pydantic schemas `Roster` / `Pointing` / `Ballot` / `DayAction`), so structured-output fidelity over the chosen surface is the load-bearing concern.

---

## 2. Alternatives Considered

### Alternative 1: Native Ollama API (`ChatOllama`)

- **Pros:** Purpose-built; native JSON-schema `format` structured output (best fidelity for the flat schemas); native model management.
- **Cons:** Ollama-specific — does not reach non-Ollama local servers; least aligned with the project's Anthropic direction (see §4).

### Alternative 2: OpenAI-compatible (`/v1/chat/completions`)

- **Pros:** One client (`langchain-openai` + base_url) reaches Ollama **and** llama.cpp / LM Studio / vLLM; the most mature, battle-tested compat layer; well-supported structured output.
- **Cons:** A translation layer that can lag Ollama-native features; an `openai` client used off-purpose; no alignment with the Anthropic direction.

### Alternative 3 (chosen): Anthropic-compatible (`/v1/messages`)

Reach Ollama through its Anthropic Messages–compatible endpoint, via an Anthropic client (`langchain-anthropic` / Anthropic SDK) pointed at a local `base_url`.

- **Pros:** Aligns with the project's original Anthropic intent (§4); a single Anthropic Messages surface that could later serve both cloud (Claude) and local (Ollama).
- **Cons:** Narrower reach than OpenAI-compat (fewer non-Ollama servers speak Anthropic Messages); Ollama's **newest / least-proven** compatibility surface; structured-output (tool-use) over it is **not yet verified** with the game's schemas.

---

## 3. Decision

The **Ollama provider implementation** (from ADR 009) talks to Ollama through its **Anthropic Messages–compatible `/v1/messages` endpoint**, using an Anthropic Messages client (`langchain-anthropic`, local `base_url`, dummy api-key, `anthropic-version` header). Structured output is obtained via Anthropic tool-use over that endpoint.

This rests on an **explicit verify-at-implementation gate:** smoke-test that tool-use / structured output over `/v1/messages` reliably produces the flat schemas with the recommended local models **before** relying on it. If it proves unreliable, fall back to **native `ChatOllama`** (Alternative 1) or **OpenAI-compat** (Alternative 2) and revisit this ADR.

---

## 4. Decision Rationale

Primary category: **strategic alignment with the project's original direction (future simplification).** Graphia's intended model family was **Anthropic Claude**; the current Nova-on-Bedrock choice (**ADR 003 — Bedrock Nova over Claude**) was a deliberate **cost-saving detour**, not an end state. Standardizing the *local* provider on the Anthropic Messages surface keeps the door open: if Nova is ever dropped for Claude, a single Anthropic client could serve **both** cloud (Claude) and local (Ollama via `/v1/messages`), collapsing two client families into one.

That strategic alignment is judged worth the accepted trade-offs — **narrower backend reach** than OpenAI-compat, and Ollama's **least-proven** compat surface. The native API (Alt 1) has the best structured-output fidelity but no path to that unification; OpenAI-compat (Alt 2) has the broadest reach but pulls the project toward an OpenAI client it otherwise has no use for. The load-bearing risk (structured-output reliability) is contained by the §3 smoke-test gate with concrete fallbacks.

---

## 5. Decision Consequences

- **Trade-offs accepted:** narrower reach than OpenAI-compat (bound to Ollama's Anthropic-compat endpoint); reliance on Ollama's newest / least-proven compatibility layer.
- **Risks / technical debt:** structured output (tool-use) over `/v1/messages` is **unverified — it must be smoke-tested** before being relied on, with native/OpenAI-compat as named fallbacks; a **third LLM client library** (Anthropic) joins Bedrock in the codebase (OpenAI deliberately not used).
- **Future implications:** positions a possible **future unification on a single Anthropic client** across cloud and local if the project returns from Nova to Claude. If the smoke-test fails or `/v1/messages` structured output later regresses, **this ADR is revisited** (switch the Ollama implementation's client — contained by the ADR 009 abstraction).

---

## 6. References

- **Architecture:** `context/product/architecture.md` — §4 (LLM Provider).
- **Related ADRs:** **009** (the provider abstraction this protocol slots into); **003** (Bedrock Nova over Claude — the cost detour this rationale references).
- **Related CRs:** _none._
- **Related specs:** `context/spec/010-local-ollama-provider/`.
- **External docs:** Ollama Anthropic-compatibility — `/v1/messages` (`docs/api/anthropic-compatibility.mdx`); Ollama structured outputs (`format`).
