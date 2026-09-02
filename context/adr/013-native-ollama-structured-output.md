# ADR 013: Native Ollama Structured Output over Anthropic Tool Use

- **ADR Number:** 013
- **Title:** Native Ollama Structured Output over Anthropic Tool Use
- **Status:** Accepted
- **Date:** 2026-09-02
- **Authors:** Alexey Tigarev

---

## 1. Context

**ADR 010 — Anthropic-Compatible Protocol for the Local Ollama Provider** chose to reach Ollama over its Anthropic Messages–compatible `/v1/messages` endpoint, and obtained structured output via Anthropic **tool use** over that endpoint. It did so knowing the risk, and wrote the risk down: structured output over `/v1/messages` was *"not yet verified with the game's schemas"*, and the decision rested on an **explicit verify-at-implementation gate** — smoke-test it, and *"if it proves unreliable, fall back to native `ChatOllama` (Alternative 1) or OpenAI-compat (Alternative 2) and revisit this ADR."*

**That gate has now returned a split result, and this ADR is the revisit it called for.**

The failure surfaced in **spec 039 (Per-AI Private Diaries)**. Its measured campaign recorded a `diary_fallback_rate` of **0.50** on the Ollama arm — 45 of 90 diary entries were the deterministic placeholder rather than model-written prose — which disqualified that arm as a diaries result. Diagnosis established the cause and, as importantly, what it was **not**:

- **The model was writing the entries; the code was discarding them.** Every failure carried `stop_reason='end_turn'` and no tool call, with a complete, in-voice entry sitting in the reply's content. Replaying six real captured prompts reproduced the failure at **11/24 (46%)**, and **11 of 11** prose replies carried a usable entry.
- **`tool_choice` does not help: the endpoint accepts it and drops it.** Forcing it two ways left `end_turn` replies at 12/18 and 6/18 against an 11/24 baseline, with Wilson intervals overlapping heavily — and decisively, an *enforced* call could not produce `end_turn` at all.
- **Ruled out by measurement:** prompt growth (fallbacks were flat — 50% on Day 1, 51% on Day 2, and Day 1 carries the smallest prompt); the 1024-token output cap (real output ran 155–693 tokens with zero truncations, and raising the cap to 4096 did not help); context overflow (32768 loaded against ~3.4k prompts); model drift (identical digest); and server contention (the two arms ran sequentially).
- **The control is decisive.** Spec 028's `Reflection` calls, on the **same model in the same runs**, failed **0 of 391**. `REFLECTION_SYSTEM` asks for a terse one-or-two-sentence note; `DIARY_SYSTEM` is a long, deliberately formless invitation to write freely. The prompt's *shape* — not its size, and not the schema — decides whether this model stays in tool-calling mode.
- **Bedrock is unaffected** (1/101 on Nova, 0/66 on Claude) because Bedrock Converse **enforces** its `toolConfig`.

So the gate's verdict is not "the endpoint is broken". It is narrower and more awkward: **tool use over `/v1/messages` is reliable for five of the game's six schemas and unreliable for the sixth**, and what separates them is a prompt-authoring choice that no type signature or review step surfaces. Direct measurement of the alternative — Ollama's native `/api/chat` with a JSON-schema `format` — returned **18/18 schema-valid** on the same prompts.

---

## 2. Alternatives Considered

### Alternative 1: Keep the Anthropic endpoint and recover the prose in the application layer

Leave the client and protocol as ADR 010 set them, and have the diary call ask for the raw reply, recovering the model's prose whenever it answers without a tool call.

- **Pros:** No dependency change and no protocol change, so ADR 010's rationale survives untouched; provider-agnostic, so it also covers any future provider with the same weakness; measured **0/24** fallbacks on the real provider; small and reviewable.
- **Cons:** Compensates for an unenforceable contract instead of enforcing it — the model may still skip the envelope, and correctness now depends on its prose being usable; works only because `Diary` has exactly **one free-text field**, so it does not generalise to `Roster` / `Pointing` / `Ballot` / `DayAction`; leaves the same latent failure in place for every other schema on this path.

### Alternative 2 (chosen): Switch the Ollama provider to the native Ollama API with JSON-schema `format`

Reach Ollama through `langchain-ollama`'s `ChatOllama` and obtain structured output from Ollama's native JSON-schema `format` — **grammar-constrained decoding**, where the sampler is masked at each step to tokens that can continue a schema-valid document.

- **Pros:** Genuine enforcement rather than a request: non-conforming output is **unrepresentable**, which is a *stronger* guarantee than Bedrock's `toolConfig` (Converse asks the model to use a tool and trusts it to comply). Measured **18/18** schema-valid. This is ADR 010's own named fallback, so taking it follows the plan rather than improvising. Protects all six schemas, not just the one that failed. Purely a **swap**: `ChatAnthropic` occurs only inside `OllamaProvider`, so `langchain-anthropic` leaves `pyproject.toml` and the client count does not grow.
- **Cons:** Abandons ADR 010's strategic rationale for the local path (see §5); needs a regression pass over the five schemas that work today, so the exposure is regression risk rather than repair; requires a provider-level seam to select the structured-output method (see §5); and the end-to-end `ChatOllama.with_structured_output(..., method="json_schema")` route is documented but **not yet measured here** — only the underlying `format` endpoint is.

### Alternative 3: Switch to the OpenAI-compatible endpoint

ADR 010's other named fallback: reach Ollama over `/v1/chat/completions` with `langchain-openai`.

- **Pros:** The broadest backend reach — the same client would also serve llama.cpp, LM Studio and vLLM; the most mature compatibility layer.
- **Cons:** Adds an `openai` client the project otherwise has no use for; abandons the Anthropic direction just as thoroughly as Alternative 2 while giving up grammar-constrained decoding for another tool-use/JSON-mode translation layer; unmeasured here, so it would trade a known-good option for an unknown one.

### Alternative 4: Keep the Anthropic endpoint and force tool use via `tool_choice`

- **Pros:** Would have been the smallest possible change — one argument, no dependency or protocol movement.
- **Cons:** **Measured and refuted.** The endpoint accepts `tool_choice` and silently ignores it; `end_turn` replies persisted at rates statistically indistinguishable from the unforced baseline. Not a viable option, recorded so it is not retried.

### Alternative 5: Rewrite `DIARY_SYSTEM` to be terse and task-shaped, like `REFLECTION_SYSTEM`

- **Pros:** No client, dependency or protocol change at all; addresses the measured trigger directly, with the 0-of-391 `Reflection` control as evidence that it would work.
- **Cons:** Treats a transport defect by degrading the product. The open, formless invitation is precisely what spec 039 chose so that different personas use a diary differently — some looking back, some ahead — instead of collapsing every player onto one shape. It also leaves the underlying fragility intact and merely stops provoking it, so the next prompt written in a literary register re-opens it.

---

## 3. Decision

**Alternative 2.** The Ollama provider's structured output moves to the **native Ollama API via `langchain-ollama`'s `ChatOllama`, using JSON-schema `format`** (`with_structured_output(Schema, method="json_schema")`). Because every gameplay LLM call on this path binds a schema — verified: the only plain `get_large()` in the package is a diagnostic tool that prints a type name — this means the Ollama provider moves to the native client wholesale, and `langchain-anthropic` is removed.

Two things are recorded as *not* part of this decision:

1. **`ChatOllama.with_structured_output(..., method="json_schema")` carries its own verify-at-implementation gate.** Ollama's native `format` endpoint is measured (18/18); the LangChain wrapper's routing to it is documented but not yet measured against the game's six schemas. Smoke-test all six before relying on it — the same discipline ADR 010 applied, and the reason this defect was caught by a plan rather than by surprise.
2. **The application-layer prose recovery already shipped** (Alternative 1, commit `b9bfbd4`) as the interim repair that unblocked measurement, and it is **not** removed by this decision. It is kept as a provider-agnostic backstop, since it costs nothing once written and covers providers this ADR does not.

**ADR 010 remains Accepted.** This ADR qualifies it: it records the gate's result and revises the structured-output mechanism, rather than retiring ADR 010's account of why the Anthropic surface was chosen. See §5 for the tension this leaves.

---

## 4. Decision Rationale

Primary category: **lowest operational risk — specifically, the integrity of the measurement layer.**

The deciding argument is not the diary feature; it is what the failure did to the evidence. A silent fallback on a non-enforcing transport turned a measured A/B arm into a half-treated one that *looked* complete, and it was caught only because spec 039 happened to instrument `diary_fallback_rate`. Graphia's quality ledger is the project's memory; a provider that can quietly substitute placeholder text for model output is a hazard to that memory, not merely to one feature. Alternative 1 makes the current symptom go away but leaves the mechanism — a request the provider may decline — in place for the other five schemas, where no equivalent health metric exists and where recovery is impossible because those schemas are not single free-text fields.

Grammar-constrained decoding removes the failure class instead of compensating for it. That is worth more than the trade-offs it costs, and those costs are smaller than they first appear: it is ADR 010's own pre-agreed fallback, it is a client **swap** rather than an addition, and it leaves all six call sites unchanged because ADR 009's provider abstraction was built for exactly this substitution. Alternative 3 abandons the Anthropic direction for no measured gain over Alternative 2. Alternative 4 is refuted. Alternative 5 is the cheapest option and was rejected on product grounds: it would buy transport reliability by deleting the feature's design intent.

The accepted cost is ADR 010's strategic rationale, which is discussed in §5 rather than dismissed here.

---

## 5. Decision Consequences

- **Complexity: net-neutral in client count, but one new seam — this was checked rather than assumed.** `ChatAnthropic` appears **only** inside `OllamaProvider` (one import, three factories), so this is a swap: `langchain-anthropic` leaves `pyproject.toml`, and the codebase still carries two client families (`langchain-aws` for both Bedrock providers, one local client). All six `with_structured_output` call sites stay **unchanged** — ADR 009's abstraction absorbing a client swap is that abstraction working as designed. The genuine increase is a **provider-level seam for the structured-output method**: native `format` needs `method="json_schema"` where Bedrock Converse needs the tool-use default, and `LLMProvider` currently hands back bare `BaseChatModel`s with the *call sites* invoking `with_structured_output` themselves. Selecting the method per provider therefore requires new indirection inside `llm.py` (a provider-supplied wrapper), because the alternative — branching at six call sites — is worse. That seam is the one real structural cost, and the implementing spec owns its shape.
- **Structured output stops being uniform across providers.** Two mechanisms now sit behind one call: tool use on Bedrock, grammar-constrained decoding on Ollama. Their failure modes differ, and the mocked suite **cannot see the difference** — every fake stubs `with_structured_output` wholesale — so the divergence is exercised only out-of-suite, by the eval harnesses. Provider-specific reliability is now a property the test suite is structurally blind to.
- **Tool use is a request, not a guarantee, and the difference is invisible in the type signature.** `with_structured_output(Schema)` reads identically across providers while meaning "enforced" on one and "usually honoured" on another. Every present and future structured-output call site inherits this asymmetry.
- **Prompt shape affects transport reliability, not only content.** A long, open, literary system prompt made this model answer in prose where a terse task-shaped one kept it calling tools — 45/90 against 0/391, same model, same runs. Prompt-authoring decisions now carry a transport consequence, and no current review step looks for it.
- **Recovery cannot serve a multi-field schema — retained as debt, not as cover.** The kept backstop works only because `Diary` has one free-text field. `Roster`, `Pointing`, `Ballot` and `DayAction` could not be recovered this way, which is precisely why the enforcement route was chosen rather than relying on the backstop.
- **Eval arms are only as valid as their fallback rates.** The spec-039 on-arm would have been read as a diaries result without `diary_fallback_rate`. Any future A/B on a provider that does not enforce needs a health metric of this kind; a silent fallback path is a measurement hazard before it is a runtime one.
- **ADR 010's unification rationale is spent for the local path, and its status will need revisiting.** ADR 010 chose the Anthropic surface to keep open a future in which a single Anthropic client serves both cloud Claude and local Ollama. After this change the local path speaks the native API, so that future requires another decision. More concretely: since every gameplay call binds a schema, `/v1/messages` will carry **no gameplay traffic at all** once this is implemented — so leaving ADR 010 marked *Accepted* is a deliberate choice to preserve its account of the reasoning, and it will likely warrant re-marking to *Superseded* at implementation time.
- **What would force this ADR to be revisited:** the `method="json_schema"` gate failing on any of the six schemas (fall back to Alternative 3, OpenAI-compat, or keep Alternative 1's recovery as the primary route); Ollama changing or dropping native `format`; or the project returning from Nova to Claude and wanting the single-Anthropic-client unification back.

---

## 6. References

- **Architecture:** `context/product/architecture.md` — §4 (LLM Provider).
- **Related ADRs:** **010** (Anthropic-Compatible Protocol for the Local Ollama Provider — the decision whose verify gate this reports on, and whose named fallback this takes); **009** (Pluggable LLM Provider Abstraction — what contains the blast radius of the client swap); **011** (Ablatable Gameplay Feature Flags — the interim recovery shipped deliberately unflagged, as a transport repair rather than a capability); **003** (Bedrock Nova over Claude — the cost detour ADR 010's unification rationale rests on).
- **Related CRs:** _none._
- **Related specs:** `context/spec/039-ai-private-diaries/` — the feature that exposed the defect, including `DIARY_SYSTEM` in `src/graphia/prompts.py` and the disqualified measured arm.
- **External docs:** Ollama structured outputs (native `format`, JSON-schema constrained decoding); Ollama Anthropic-compatibility — `/v1/messages`; LangChain structured output — `with_structured_output(schema, method="json_schema")` for provider-native structured output.
