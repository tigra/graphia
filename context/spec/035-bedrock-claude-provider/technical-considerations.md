<!--
Technical considerations for spec 035 — Bedrock Claude (Haiku) Provider.
HOW a third gameplay provider is wired, verification-spike-first, behind config selection.
-->

# Technical Specification: Bedrock Claude (Haiku) Provider

- **Functional Specification:** `./functional-spec.md`
- **Status:** Completed
- **Author(s):** Alexey Tigarev

> **Backed by ADR-012** (Bedrock model selection — Haiku 4.5 gameplay profile). **Verification-spike-FIRST** per the functional spec: prove the gameplay path reaches Claude **locally and on the deployed runtime** (the historically brittle part) *before* building the provider out.

> **Shared surface with spec 034 (in flight):** both edit `llm.py` (034: a higher-temperature persona model / `get_large` override; 035: the provider resolution + the Bedrock branch) and `config.py` (034: persona flags; 035: the provider value + per-tier model ids). They are **NOT disjoint** — expect to combine both in `_resolve_provider`/the Bedrock branch and the `GRAPHIA_LLM_PROVIDER` parse on merge (the 028/030 pattern), not blind parallel edits.

---

## 1. High-Level Technical Approach

Add a third value to the existing provider selection (`config.llm_provider` / `_resolve_provider()` in `llm.py`): **`bedrock-claude`**, alongside `bedrock` (Nova, default) and `ollama`. Its `large()`/`small()` return `ChatBedrockConverse` instances pointed at **Claude (Haiku)** model ids (per-tier overridable, with documented defaults), reusing the same `region_name=config.aws_region` construction the Nova branch already uses. Nova and Ollama branches are **untouched**. Selection is a single config change (`GRAPHIA_LLM_PROVIDER=bedrock-claude`), no source edits; Nova stays the default when nothing is set.

Because the **deployed-runtime path to Claude has historically been the brittle part** (regional routing / model access), the work is sequenced **spike-first**: a local spike proves `get_large()` reaches Claude *and* the game's flat Pydantic schemas round-trip on Claude via Bedrock Converse; a deployed spike proves the **hosted AgentCore runtime** can reach Claude (confirmed from the runtime's own CloudWatch telemetry, not inferred locally) — both before building out error-handling, docs, and the full provider surface.

Affected files: `src/graphia/config.py` (provider value + per-tier model-id config), `src/graphia/llm.py` (the `bedrock-claude` branch), a Claude preflight + plain-message mapping (mirroring `preflight.run_ollama_preflight`), docs, and tests. Possibly the deployed runtime's IAM/model-access (terraform) for the deployed spike. **Unchanged:** game rules, the Nova/Ollama branches, `METRICS_VERSION`, the `Persona`/schema definitions.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — Local verification spike (FIRST, before build-out)

- A minimal, developer-run check (a `make` target / small script, e.g. `make claude-spike`, or an opt-in real-model test like `test_remote_observability_live`'s gating) that, with `GRAPHIA_LLM_PROVIDER=bedrock-claude` and live creds, calls `get_large()` once and **round-trips one structured output** (e.g. a `Ballot` or `DayAction` — the flat Pydantic schemas; Bedrock Converse rejects discriminated unions, so confirm Claude+Converse accepts the project's flat schemas). Proves the path + the structured-output contract before further work. **Verify at implementation:** the exact Claude Haiku Bedrock model id / `us.`-prefixed inference profile and its region availability (ADR-012 names Haiku 4.5; confirm the live Bedrock id, do not hard-code unverified).

### Component B — Provider wiring (`config.py`, `llm.py`)

- **`config.py`:** extend the `GRAPHIA_LLM_PROVIDER` parse to accept `bedrock-claude` (keep `bedrock`/`ollama`; update the error message). Add **per-tier model-id config** with documented defaults — `large_model` / `small_model` fields (env `GRAPHIA_LARGE_MODEL` / `GRAPHIA_SMALL_MODEL`), defaulting to the Claude Haiku ids under `bedrock-claude` (and leaving Nova's ids as the `bedrock` defaults). This generalises today's **hardcoded** Nova ids in `llm.py` into config-driven, overridable values.
- **`llm.py`:** add a `case "bedrock-claude"` to `_resolve_provider()` returning a provider whose `large()`/`small()` build `ChatBedrockConverse(model=<resolved large/small id>, region_name=config.aws_region, temperature=…)`. Factor the Bedrock construction so Nova and Claude share it (parameterised by model id), leaving Nova's observable behavior identical. Mutually exclusive per run (the existing single-provider resolution already guarantees this).

### Component C — Clear feedback when Claude can't be reached (`preflight.py`, error mapping)

- A **Claude preflight** mirroring `run_ollama_preflight`: before games, verify credentials, model access, and region, raising `SystemExit` with **plain-language, actionable** messages — *expired/missing credentials → how to refresh*, *missing model access / wrong region → how to enable* — and **no stack trace** (catch the boto3/Bedrock `AccessDenied` / `UnrecognizedClient` / `ValidationException` families and map them). Mid-game unusable outputs continue gracefully via the **existing** retry-then-deterministic-fallback safety nets (no new mechanism).

### Component D — Deployed-runtime verification spike (developer-run, live AWS)

- Confirm the **hosted AgentCore runtime** can reach Claude: with the runtime configured for `bedrock-claude`, run a full game on it and read the **runtime's CloudWatch telemetry** to prove Claude served the calls (per the "verify deployed state, don't infer locally" project principle). May require an **IAM/model-access change** (terraform) so the runtime's role can invoke the Claude model. This is the historically brittle path the spec insists on proving — **out of the offline suite; developer-run with the deployment.**

### Component E — Docs

- A **quickstart** (README / docs) for selecting each provider (`GRAPHIA_LLM_PROVIDER` = `bedrock` | `bedrock-claude` | `ollama`), naming the Claude **default model ids** and the per-tier override env vars.

### What does NOT change

- Nova (`bedrock`) and Ollama branches; game rules / turn order / win detection; the career greeting/panel; `METRICS_VERSION`; the schemas. No ablation flag — a *provider* is config-selected (Nova remains the default), not a gameplay-feature toggle.

---

## 3. Impact and Risk Analysis

- **Deployed-runtime reachability (the headline risk).** Historically brittle (regional routing / model access). *Mitigation:* the spike-first sequence (Component D) proves it before build-out; an IAM/model-access (terraform) change may be needed for the runtime role.
- **Structured-output round-trip on Claude.** The project's flat-schema constraint exists because Bedrock Converse rejects discriminated unions; Claude-via-Converse must accept the existing flat `Roster`/`Ballot`/`Pointing`/`DayAction`. *Mitigation:* the local spike (Component A) round-trips one before committing.
- **Model id / region (check-don't-guess).** The exact Claude Haiku Bedrock id + inference profile + region availability are verified at the spike, not hard-coded on faith.
- **Cost.** Claude Bedrock tokens cost more than Nova; opt-in (Nova stays default), operator-chosen.
- **Shared surface with 034.** `llm.py` + `config.py` overlap — merge by combining both (Claude branch + persona temperature; provider/model config + persona flags), per the 028/030 precedent.
- **`METRICS_VERSION` / determinism.** Unchanged; the dual-mode byte-equal smoke is provider-pinned and unaffected by an additive provider.

---

## 4. Testing Strategy

- **Offline (mocked) suite:** config parse accepts `bedrock-claude` and resolves the documented default model ids (and honors `GRAPHIA_LARGE_MODEL`/`GRAPHIA_SMALL_MODEL` overrides); `_resolve_provider()` returns Claude-configured `ChatBedrockConverse` instances **without a live call** (assert the constructed model ids/region, mock at the boundary); Nova/Ollama resolution is **unchanged** (regression); the preflight maps representative boto3 error families to plain messages (no stack trace) on a faked client; `safe_llm` keeps every test off real Bedrock. Full `uv run pytest -q` green.
- **Local spike (developer-run, real Bedrock):** `make claude-spike` reaches Claude and round-trips a structured output.
- **Deployed spike (developer-run, live AWS + deployment):** a full game on the hosted runtime served by Claude, confirmed from the runtime's CloudWatch telemetry.
- **Manual:** switching `GRAPHIA_LLM_PROVIDER` among the three plays a full local game on each; unset ⇒ Nova, behavior identical to today.
