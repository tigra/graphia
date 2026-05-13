# ADR 003: Bedrock Model Family — Amazon Nova (Pro + Lite) over Anthropic Claude

- **ADR Number:** 003
- **Title:** Bedrock Model Family — Amazon Nova (Pro + Lite) over Anthropic Claude
- **Status:** Accepted
- **Date:** 2026-05-13
- **Authors:** Alexey Tigarev

---

## 1. Context

Spec 002 (Hosted AgentCore Deployment) inherits spec 001's `chatbedrockconverse-singleton` + `regional-inference-profile-prefix` design: two `ChatBedrockConverse` singletons — Sonnet for gameplay, Haiku for roster generation — invoked via the `us.anthropic.claude-sonnet-4-5-...` and `us.anthropic.claude-haiku-4-5-...` cross-region system inference profile IDs in us-east-1. The Slice 4 USER smoke test surfaced two compounding problems with this posture:

- **Cross-region Marketplace auto-subscribe denial.** The `us.*` profile fans inference out to us-east-1 / us-east-2 / us-west-2 based on routing. The runtime role can call Bedrock `InvokeModel` in those destination regions only if the underlying model's Marketplace subscription is active there. Subscriptions don't propagate across regions; the role would have to either be granted `aws-marketplace:Subscribe` (account-level, no resource scoping) to auto-subscribe per-region, or the operator would have to manually enable model access via the console in each destination region. The first game in remote mode worked because routing landed in us-east-1 (already subscribed); subsequent games flaked when routing landed elsewhere.
- **Inference-profile complexity is over-scoped for a personal reference project.** ADR 001 framed Graphia as a personal reference / learning artifact; the operational + IAM surface of cross-region inference profiles (sticky-session routing, region-wildcard IAM, per-region Marketplace) goes beyond what spec 002's "demonstrate hosted AgentCore" goal pedagogically needs.

A probe round during the smoke test also discovered the Claude family no longer has a workable direct-on-demand path: Sonnet 3 is legacy + inactive, Opus 3 / Sonnet 3.5 v1+v2 / Sonnet 3.7 are end-of-life, Haiku 3.5 + all 4.x are inference-profile-only, and only Claude 3 Haiku still supports direct on-demand — but Haiku 3 is too small for the Mafia roleplay tasks the gameplay singleton drives.

The user explicitly framed this round as workflow validation rather than gameplay optimisation: *"OK to test the workflow, not necessarily the ideal gameplay experience — defer optimising it for now."* That tactical framing is what makes this decision a sensible candidate to ship today and revisit later.

---

## 2. Alternatives Considered

### Alternative 1: Claude 4.5 via `us.*` cross-region profile + `aws-marketplace:*` permissions on the runtime role

Keep spec 001's `us.anthropic.claude-sonnet-4-5` + `us.anthropic.claude-haiku-4-5` model IDs; restore the inference-profile entries in the IAM policy; grant `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`, `aws-marketplace:ViewSubscriptions` to the runtime role so it can auto-subscribe in destination regions during the first fan-out.

- **Pros:**
  - Best gameplay quality — Sonnet 4.5 is top-tier for the Mafia roleplay + deception + Day-phase chat richness the gameplay singleton drives.
  - Preserves spec 001's `regional-inference-profile-prefix` concept; no retirement note needed in the tutorial trail.
  - AWS-recommended pattern for high-demand Claude on Bedrock; stays inside the documented happy path.
- **Cons:**
  - Role gets broad `aws-marketplace:*` permissions. These actions have no resource-level scoping in AWS — the role can subscribe to *any* Marketplace product on the account, not just Bedrock models.
  - Cross-region routing brings sticky-session + IAM-scope surface. The `agentcore_client` already had to grow `runtimeSessionId` stability for this; the IAM had to wildcard the foundation-model region; CloudWatch traces land in whichever region routing picked.
  - Doesn't fully solve the Marketplace flake — just papers over it. Subscriptions in destination regions can still be pending or flaky on first invocation; the role's auto-subscribe just initiates the request.

### Alternative 2: Application inference profile in us-east-1 wrapping Claude 4.5

Create `aws_bedrock_application_inference_profile` Terraform resources wrapping the Sonnet 4.5 and Haiku 4.5 foundation models in us-east-1 only; flow the profile ARNs into the Runtime container as env vars; have `llm.py` read them at boot (with a local-mode fallback path).

- **Pros:**
  - Single-region routing — no cross-region IAM surface, no Marketplace cross-region concern.
  - Tight IAM scoping to one profile ARN per model (no foundation-model region wildcards).
  - Best gameplay quality (Sonnet 4.5 stays the gameplay model; the profile is just a wrapper that satisfies the on-demand-throughput constraint).
  - Reproducible from a fresh account — the profile is Terraform-managed; no clickops dependency.
- **Cons:**
  - Complexity of inference profile setup — new TF resource per model, env-var plumbing from Terraform output into the Runtime container, local-mode fallback story for developers. More moving parts than two model-ID constants.

### Alternative 3: Claude 3 Haiku for both Sonnet and Haiku roles

Set `_SONNET_MODEL_ID = _HAIKU_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"`. Single model, single direct-on-demand call path, no profile.

- **Pros:**
  - Stays in the Anthropic family — preserves `chatbedrockconverse-singleton`'s vendor continuity.
  - Verified to work for direct on-demand (probe today against real Bedrock confirmed clean response).
  - Simplest possible config — single model ID, single IAM ARN pattern, no profile.
  - Cheap per-token — Haiku 3 is the cheapest current Anthropic on Bedrock.
- **Cons:**
  - Still locked to a single Anthropic model that could be deprecated — Haiku 3 is the *only* surviving direct-on-demand Claude; if AWS retires it next, we're back to the inference-profile dance.
  - Haiku 3 too small for gameplay roleplay quality. Designed for triage / classification, not the rich Day-phase chat + deception logic Sonnet 4.5 powered. Significant qualitative drop.

### Alternative 4 *(chosen)*: Amazon Nova Pro (gameplay) + Nova Lite (roster), direct on-demand in us-east-1

Set `_SONNET_MODEL_ID = "amazon.nova-pro-v1:0"` and `_HAIKU_MODEL_ID = "amazon.nova-lite-v1:0"`. IAM policy scoped to `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*`. No inference profile, no cross-region routing.

- **Pros:**
  - Direct on-demand works in us-east-1 — no profile needed. Verified today against real Bedrock; Nova Pro, Nova Lite, and Nova Micro all invoke cleanly with the four flat structured-output schemas (`Roster`, `Pointing`, `Ballot`, `DayAction`).
  - No Marketplace cross-region concern — single-region invocation eliminates the entire auto-subscribe failure mode that derailed the `us.*` path.
  - Opportunity to test the Nova family — this round was already an exercise in the deployment loop; Nova exercises an additional model-family path. Spec 002's central claim is *workflow* validation; different models satisfy that as well as different gameplay quality would.
  - Two-tier model split preserved — Nova Pro + Nova Lite gives the same capable-for-gameplay / cheap-for-mechanical-work shape spec 001's design assumed. The `chatbedrockconverse-singleton` concept still applies; the singletons just hold different models.
- **Cons:**
  - Gameplay-roleplay quality drop vs. Sonnet 4.5 (deferred concern; revisit if Day-phase chat feels thin).
  - Retires spec 001's `regional-inference-profile-prefix` concept — tutorial 001 stays as a historical record of what the playable skeleton genuinely did at the time, but later tutorials need a "concepts retired" note.
  - Vendor lock shifted from Anthropic to Amazon Nova — trades one model-vendor dependency for another. Symmetric risk, not strictly better.
  - Different prompt sensitivity than Claude — Nova may need prompt tuning that worked for Claude to be revisited (especially Day-phase chat style). Structured-output schemas verified; prose quality may want iteration in a later spec.

---

## 3. Decision

Adopt **Alternative 4**: Amazon Nova Pro for the gameplay singleton, Nova Lite for the roster singleton, invoked directly against the foundation-model ARN in `us-east-1` with no inference profile. The runtime IAM execution role grants `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` on the single ARN pattern `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*`.

Code change is contained to two constants in `src/graphia/llm.py`; IaC change is contained to the `bedrock_invoke_resources` list in `infra/terraform/locals.tf`. Shipped in commit `729666c`.

---

## 4. Decision Rationale

The primary rationale is *fastest path to a working deploy loop for Slice 4's smoke goals*, accepting a gameplay-quality trade-off as a tactical, reversible choice.

All Claude alternatives carry operational surface that exceeds the value for a personal reference project — the `us.*` profile path requires `aws-marketplace:Subscribe` on the runtime role (account-wide, no resource-level scoping); the application inference profile path requires new Terraform resources, env-var plumbing from Terraform output into the Runtime container, and a local-mode fallback story. Both spend complexity that Slice 4 doesn't need to spend.

Nova satisfies the same workflow goals — spec 002's central claim is *workflow* equivalence, and the model family the graph happens to call doesn't change whether the AgentCore Runtime, the wire format, the IAM execution role, or the HITL round-trip all work correctly.

Gameplay quality is deferred, not abandoned: Nova Pro is a capable model, the structured-output schemas already round-trip, and the model IDs are isolated to two constants in `llm.py` — a future CR/ADR can revisit and pick the Claude+profile path if gameplay output proves too thin.

Primary rationale category: **lowest cost / fastest to ship**.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- Lower gameplay-roleplay quality vs. Sonnet 4.5 — deferred concern; revisit if Day-phase chat feels thin.
- Retired spec 001's `regional-inference-profile-prefix` concept — tutorial 001 stays as a historical record; later tutorials need a "concepts retired" note.
- Vendor shift Anthropic → Amazon Nova — symmetric vendor lock, not strictly better.

**Future implications:**

- Tutorial 002 (interim) needs a "concepts retired" note when next regenerated, marking `regional-inference-profile-prefix` as no longer in use.
- Gameplay-quality optimisation is now a deliberate future spec — if Nova Pro proves too thin for Day-phase chat richness or Mafia deception, a follow-up CR/ADR can revisit and switch families. Model IDs are isolated to two constants; the reversal is bounded.

**Technical debt incurred:**

- None material. The reverse-cost is two constants in `llm.py` plus one IAM ARN pattern in `locals.tf`. The IAM policy is already minimal. Reversing is a small CR/ADR, not a migration.

---

## 6. References

_None recorded._
