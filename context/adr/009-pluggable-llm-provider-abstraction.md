# ADR 009: Pluggable LLM Provider Abstraction (Bedrock + local Ollama)

- **ADR Number:** 009
- **Title:** Pluggable LLM Provider Abstraction (Bedrock + local Ollama)
- **Status:** Accepted
- **Date:** 2026-06-11
- **Authors:** Alexey Tigarev

---

## 1. Context

Roadmap **Phase 4 — AI Provider Flexibility → Local Ollama Provider** and the product definition's goal of "truly offline play" require letting a player reach the game's AI **without AWS**. Today the only path is Amazon Bedrock — an AWS account, credentials, and per-token spend — which blocks contributors with no cloud access or no budget. Functionally (spec 010) the game must run **fully offline** against a local Ollama model.

The whole game already reaches the LLM through **one seam**: the `get_large()` / `get_small()` factory in `src/graphia/llm.py`, called everywhere as `get_X().with_structured_output(<Schema>).invoke(...)`. The decision is *how* to admit a second provider behind that seam without disturbing the call sites.

---

## 2. Alternatives Considered

### Alternative 1 (chosen): Abstract provider interface + two implementations, selected by a factory branch

A normal provider abstraction — an abstract provider interface with a **Bedrock** implementation and an **Ollama** implementation — with the existing `get_large()`/`get_small()` factory **branching on a provider setting** to pick one.

- **Pros:** Smallest change — the provider-agnostic call sites (`.with_structured_output(...).invoke(...)`) are untouched; keeps the established two-tier (large/small) pattern.
- **Cons:** A bit more structure than a bare inline conditional; introduces a new local-LLM client dependency.

### Alternative 2: Formal strategy / registry abstraction

A provider *registry* where implementations self-register and are looked up by name.

- **Pros:** Cleaner extensibility for many future providers.
- **Cons:** Over-engineered for two providers (YAGNI); more indirection to maintain; no capability gain over a two-implementation abstraction — against the project's minimal-abstraction stance ("two tiers is the cap; variation from prompts, not more machinery").

### Alternative 3: Bare inline branch (no abstraction)

A plain conditional inside the factory (`if provider == "ollama": … else: …`), with no provider interface.

- **Pros:** Least code.
- **Cons:** Ad hoc; no clean separation between providers; harder to test or extend as a unit. (This was the first-pass shape, deliberately upgraded to a real abstraction.)

### Alternative 4: Do nothing — Bedrock only

Decline offline support; rely solely on the sibling AWS-credential-flexibility item so the only path to the LLM stays Bedrock.

- **Pros:** No new dependency or code.
- **Cons:** Fails the Phase 4 roadmap item and the "truly offline play" goal; leaves no-cloud / no-budget contributors blocked.

---

## 3. Decision

Adopt **Alternative 1**: introduce a normal LLM-provider abstraction — an abstract provider interface with a **Bedrock** implementation and an **Ollama** implementation — and have the existing `get_large()`/`get_small()` two-tier factory **branch on a provider setting** to select the implementation. The Ollama provider is **local-mode only** (a deployed Runtime cannot reach a model on the player's machine). The **protocol/client** the Ollama implementation uses to talk to Ollama (native API vs OpenAI-compatible vs Anthropic-compatible vs other) is a **separate decision recorded in its own ADR**, and must slot into this interface.

---

## 4. Decision Rationale

Primary category: **best fit for the realistic need.** Two providers want exactly a two-implementation abstraction — no more, no less. The factory branch keeps the change contained to the one seam every call site already passes through, preserving the two-tier pattern and leaving nodes, graph, state, schemas, and UI untouched. The heavier registry (Alt 2) buys extensibility the project doesn't need and contradicts its minimal-abstraction stance; the bare inline branch (Alt 3) saves a little structure at the cost of testability and clean separation; doing nothing (Alt 4) fails the roadmap goal outright. The accepted trade-offs — slightly more structure than an inline branch, and a new local-LLM dependency — are small and contained.

---

## 5. Decision Consequences

- **Trade-offs accepted:** a bit more structure than a bare inline branch; a new local-LLM client dependency enters the codebase.
- **Future implications:** the **architecture document must be revised** — "LLM Client: `ChatBedrockConverse`" and "local mode hits AWS only for Bedrock model invocation" no longer hold, since local mode can now run fully offline; any **future provider** must implement this interface and slot into the factory; the Ollama **protocol/client choice is a separate, still-open decision** (its own ADR) that this abstraction must accommodate.
- **Technical debt / risks:** two client families now live behind the factory and must both be kept working as dependencies evolve; the **test suite must exercise both provider branches**; and there is a real **risk that the two implementations behave significantly differently** (Bedrock/Nova vs a local Ollama model) — local models need **evaluation** for dialogue quality and structured-output reliability (the dialogue-diversity eval harness from spec 009 is the tool for that). Reversal is cheap: drop the Ollama implementation and the provider setting.

---

## 6. References

- **Architecture:** `context/product/architecture.md` — §4 (LLM Provider) and §3 (Deployment Topology / local mode) — *needs revision per §5.*
- **Related ADRs:** 001 (Hosted Runtime + local mode preserved), 003 (Bedrock Nova over Claude); a forthcoming ADR for the **Ollama protocol/client** choice.
- **Related CRs:** _none._
- **Related specs:** `context/spec/010-local-ollama-provider/`.
- **External docs:** Ollama — <https://ollama.com> (structured outputs; API / OpenAI- & Anthropic-compatibility).
