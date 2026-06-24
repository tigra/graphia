# ADR 012: Bedrock Model Selection — Haiku 4.5 Gameplay Profile (alongside Nova) + Opus 4.8 Eval Judge

- **ADR Number:** 012
- **Title:** Bedrock Model Selection — Haiku 4.5 Gameplay Profile (alongside Nova) + Opus 4.8 Eval Judge
- **Status:** Proposed
- **Date:** 2026-06-23
- **Authors:** Alexey Tigarev

---

## 1. Context

ADR-003 (Bedrock Nova over Claude) chose Amazon Nova Pro/Lite as a tactical "validate the workflow, not the gameplay" move, **explicitly deferring** gameplay-quality optimisation to a later decision. That decision is now due: we want to **evaluate a more capable gameplay model without losing the Nova baseline** — Nova remains the default and is periodically tested while a Claude profile is trialed alongside it.

Separately, the persona/dialogue evaluation work (blunder-eval, persona-similarity specs) needs a **strong, independent model to score outputs** (LLM-as-judge); ad-hoc/manual scoring isn't reproducible. This ADR selects concrete Bedrock models for both purposes and records their inference profiles and prices. The mechanism for admitting another model is the provider/factory seam established by ADR-009 (Pluggable LLM Provider Abstraction); the local Ollama provider (ADR-010) is the third path behind that seam.

All profiles below were verified available on the account's Bedrock in **us-east-1** on 2026-06-23.

---

## 2. Alternatives Considered

### Alternative 1 *(chosen)*: Haiku 4.5 for both tiers (alongside Nova) + Opus 4.8 eval judge

A new Bedrock profile selecting Claude Haiku 4.5 for **both** the large (gameplay) and small (mechanical) tiers, selectable alongside the existing Nova profile; Nova stays the default, periodically-tested baseline. Opus 4.8 adopted as an eval-only judge, never in the gameplay path.

- **Pros:**
  - Keeps Nova as a tested baseline — no capability dropped while a stronger model is trialed.
  - Near-frontier gameplay at low cost — Haiku 4.5 is far stronger than Nova for roleplay/deception yet still cheap ($1/$5 per 1M).
  - Independent, capable eval judge — Opus 4.8 scoring is more discriminating and avoids self-judging bias.
  - Slots into the existing provider seam (ADR-009) — additive config; call sites untouched.
- **Cons:**
  - Higher running cost when the Haiku profile / Opus judge are active vs Nova-only.
  - Three model families to keep working and tested (Nova, Haiku/Claude gameplay, Opus judge).
  - Haiku-for-the-large-tier may still produce thinner prose than Sonnet.
  - Re-introduces an Anthropic dependency that ADR-003 had moved away from.

### Alternative 2: Status quo — Nova Pro + Nova Lite only, ad-hoc judging

Keep Nova as the only Bedrock models; add no Haiku profile and pin no judge; continue scoring evals ad hoc.

- **Pros:**
  - No new dependency or config.
  - Lowest per-token cost (no Opus judge spend).
  - Nothing new to keep tested.
- **Cons:**
  - Leaves ADR-003's deferred gameplay-quality question open.
  - No strong, independent judge for the persona-similarity (specs 032/033) and blunder-eval work.
  - Ad-hoc model trials aren't pinned to profiles/prices — not reproducible or comparable.

### Alternative 3: Mixed Claude profile — Haiku 4.5 (small) + Sonnet 4.6 (large)

Keep Haiku 4.5 for the small/mechanical tier but use Sonnet 4.6 for the large/gameplay tier, instead of Haiku 4.5 for both.

- **Pros:**
  - Best gameplay prose of the options — Sonnet 4.6 is strongest for Day-phase chat, deception, persona richness.
  - Closest to Graphia's original pre-ADR-003 (Sonnet-class gameplay) design.
- **Cons:**
  - Large-tier cost ~3× the Haiku-for-both profile (Sonnet 4.6 $3/$15 vs Haiku 4.5 $1/$5).

---

## 3. Decision

Adopt **Alternative 1**. Introduce a Bedrock model **profile** that selects **Claude Haiku 4.5 for both the large (gameplay) and small (mechanical) tiers**, selectable alongside the existing **Amazon Nova** profile (Nova Pro + Nova Lite) through the ADR-009 `get_large()`/`get_small()` provider seam. **Nova remains the default baseline and is periodically tested.** Adopt **Claude Opus 4.8 as a dedicated LLM-as-judge** for evals (persona similarity, blunder-eval scoring), invoked **only from the eval harness — never the gameplay path**.

Selected models, inference profiles, and list prices (us-east-1, verified 2026-06-23):

| Role | Model | Bedrock inference profile | Price (in / out per 1M) |
|---|---|---|---|
| Gameplay + mechanical (new profile, both tiers) | Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` (also `global.…`) | $1 / $5 |
| Eval judge (LLM-as-judge, eval harness only) | Claude Opus 4.8 | `us.anthropic.claude-opus-4-8` (also `global.…`) | $5 / $25 |
| Baseline (unchanged, retained + periodically tested) | Amazon Nova Pro / Nova Lite | direct on-demand `amazon.nova-pro-v1:0` / `amazon.nova-lite-v1:0` | per ADR-003 |
| *Considered, not chosen* (Alt 3 large tier) | Claude Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | $3 / $15 |

---

## 4. Decision Rationale

Primary category: **lowest cost / fastest to ship.** Selecting concrete, already-available Bedrock profiles and recording them is the fastest, lowest-effort way to start comparative gameplay evals and to stand up an independent judge — a config-level change that slots into the existing ADR-009 seam with no new infrastructure, and keeping Nova as the default baseline makes it low-risk and reversible. The **status-quo** option (Nova-only, ad-hoc judging) was rejected because it leaves ADR-003's deferred quality question open and gives evals no independent, reproducible judge. The **mixed Haiku-small / Sonnet-large** profile was rejected *for now* because its large-tier cost is ~3× the Haiku-for-both profile while Sonnet-tier prose is likely more than this reference project needs — it remains the obvious upgrade if Haiku-for-the-large-tier proves thin.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- Higher running cost when the Haiku profile / Opus judge are active vs Nova-only.
- Three model families to keep working and tested (Nova, Haiku/Claude gameplay, Opus judge).
- Re-introduces an Anthropic dependency that ADR-003 had moved away from.
- Forgoes Sonnet 4.6's stronger large-tier gameplay prose (by using Haiku 4.5 for the large tier).

**Future implications:**

- Any future gameplay-model profile must slot into the ADR-009 provider/factory seam, not bypass it.
- Keeping Nova available and periodically tested is now an ongoing maintenance commitment.
- Evals gain an LLM-as-judge implementation opportunity (Opus 4.8 scoring) as a standing capability.
- These Claude/Bedrock models must be made to work **beyond local calls — in the deployed AgentCore Runtime** — which re-confronts ADR-003's cross-region Marketplace/IAM friction. (Today's smoke calls worked only because they ran locally from a broad developer SSO principal; a `us.` profile call routes non-deterministically across us-east-1/us-east-2/us-west-2, so they prove model access in *at least one* of the three regions — not us-east-1 specifically.)

**Technical debt incurred:**

- **Three** gameplay providers/profiles must all keep passing: the Nova baseline, the new Haiku 4.5 profile, **and** the local Ollama provider (ADR-009/010).
- Eval cost grows with Opus 4.8 judge usage ($5/$25 — the priciest model in play).
- Deployed-context wiring debt: the Claude/Bedrock profile is proven only via local smoke calls; running it in the AgentCore Runtime re-opens ADR-003's Marketplace/IAM friction — and there is **no single-region escape** (see the verified constraint below).

**Verified constraint (probe, 2026-06-23) — no single-region inference path for Claude 4.x on Bedrock.** Direct on-demand invocation of the foundation model is unsupported (`400 — on-demand throughput isn't supported`), and an application inference profile **cannot pin to one region**: `copyFrom` the foundation-model ARN is rejected (`ValidationException — does not support On Demand inference`), while `copyFrom` the `us.` system profile inherits **all three** member regions (us-east-1, us-east-2, us-west-2). Consequently the deployed runtime role must permit `bedrock:InvokeModel` / `InvokeModelWithResponseStream` on the inference-profile ARN **plus** the foundation-model ARN in **all three** regions, with Marketplace model access enabled in each — the broad cross-region surface ADR-003 sought to avoid, now unavoidable. ADR-003's "Alternative 2" (application inference profile pinned to us-east-1) does **not** apply to inference-profile-only models; it only ever worked for on-demand-capable models like Nova.

---

## 6. References

- **Architecture:** `context/product/architecture.md` — §4 (LLM Provider) and §3 (Deployment Topology / local mode)
- **Related ADRs:** 003 (Bedrock Nova over Claude), 009 (Pluggable LLM Provider Abstraction), 010 (Anthropic-compatible Ollama Protocol)
- **Related CRs:** _none._
- **Related specs:** `context/spec/010-local-ollama-provider/`, `context/spec/011-ai-blunder-tracking/`
- **External docs:** _none._
