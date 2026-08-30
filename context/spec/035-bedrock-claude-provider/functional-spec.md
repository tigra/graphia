# Functional Specification: Bedrock Claude (Haiku) Provider

- **Roadmap Item:** New **Phase 4 — AI Provider Flexibility** sub-item: *Bedrock Claude (Haiku) Provider*. Not yet listed on the roadmap (Phase 4's existing sub-items are complete); backed by **ADR-012** (Bedrock Model Selection — Haiku 4.5 gameplay profile + Opus 4.8 eval judge). A roadmap tick should be added as a follow-up.
- **Status:** Completed *(verified 2026-08-30 — all 19 acceptance criteria met; suite green at 1160 passed / 1 skipped; Claude confirmed on BOTH paths from telemetry: deployed runtime via its own CloudWatch logs, local game via Bedrock `Invocations` metrics)*
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today Graphia's AI runs on one of two engines, chosen before launch: **Amazon Nova** on the cloud (the default) or a **local Ollama** model. Nova was always a tactical default — picked because it was the fastest, least-fragile way to get a working cloud deployment — and the project deliberately deferred trying a more capable cloud model for gameplay.

This change adds a **third AI-provider choice: Anthropic Claude (Haiku) on the cloud**, selectable exactly the way the other engines are — a single configuration choice, no code changes. The operator can then run games and evaluations on a stronger, near-frontier model, while **Amazon Nova stays untouched as the periodically-tested baseline** to compare against and fall back to.

Claude is offered **both for local play/evaluation and for the hosted (deployed) runtime**. Reaching Claude on the cloud has historically been the brittle part — it was set aside earlier for *deployment-plumbing* reasons, not model quality (the hosted runtime couldn't reliably reach the model across the cloud's regional routing). So this work **deliberately begins by verifying the path end-to-end — locally *and* on the deployed runtime — before building the provider out**, so we don't discover a blocker only after committing to the full implementation.

**Desired outcome:** an operator can switch the AI engine to Claude (Haiku) with one configuration change and play or evaluate a full game on it — locally, and on the hosted runtime once the path is verified — with Nova and Ollama unaffected.

**Success is measured by:** with Claude selected, a full game completes (Night → Day → … → end state) using Claude for all AI behavior, **both locally and on the deployed runtime**; switching back to Nova or Ollama (or configuring nothing) leaves existing behavior exactly as it is; and when Claude can't be reached, the operator sees a **clear, plain-language message** (expired credentials, missing model access, wrong region) instead of a raw stack trace.

---

## 2. Functional Requirements (The "What")

### 2.1 Choose Claude (Haiku) as the AI provider

- **As an** operator, **I want** to select Anthropic Claude (Haiku) as the game's AI provider through configuration, **so that** games and evals run on a stronger cloud model without any code change.
  - **Acceptance Criteria:**
    - [x] The configuration offers **Claude (Haiku)** as a provider option alongside the existing **Amazon Nova** (cloud default) and **local Ollama** choices, selected before launch in the same place the provider is already chosen.
    - [x] Given Claude is selected with working cloud credentials, when the operator launches, then the game plays normally using Claude for all AI behavior.
    - [x] Given nothing is configured for the provider, when the operator launches, then behavior is exactly as today (Amazon Nova) — existing users are unaffected.

### 2.2 Works both locally and on the deployed runtime

- **As an** operator, **I want** Claude available both for local play/evals and on the hosted runtime, **so that** I can evaluate and demo it everywhere the game runs.
  - **Acceptance Criteria:**
    - [x] Given Claude is selected, when the operator plays locally, then a full game completes on Claude.
    - [x] Given Claude is selected, when a game runs on the deployed (hosted) runtime, then a full game completes on Claude, with the hosted runtime permitted to reach the model.
    - [x] The deployed path is confirmed by observing **actual successful runs on the hosted runtime** (read from the runtime's own telemetry), not inferred from local calls alone.

### 2.3 Haiku for both tiers, with overridable models

- **As an** operator, **I want** Claude (Haiku) to power both the gameplay work and the lighter mechanical work by default, with the ability to override either model, **so that** I get sensible defaults but can tune later.
  - **Acceptance Criteria:**
    - [x] By default, Claude (Haiku) handles both the **gameplay tier** (AI dialogue, night targeting, votes) and the **lighter tier** (AI name generation).
    - [x] The operator can **independently override** the model used for each tier through configuration.
    - [x] Given the operator overrides nothing, when they select Claude, then **documented default models** are used, so selecting Claude works out of the box.

### 2.4 Easy, no-code switching between providers

- **As an** operator, **I want** switching among Nova, Claude, and Ollama to be a single configuration change, **so that** comparing engines is friction-free.
  - **Acceptance Criteria:**
    - [x] Switching the active provider requires only a **configuration change — no source edits**.
    - [x] For a given run the three providers are **mutually exclusive**; the selected one powers all AI behavior for that run.
    - [x] Documentation states how to select each provider and names the Claude default models.

### 2.5 Clear feedback when Claude can't be reached

- **As an** operator, **I want** a clear, plain-language message when Claude can't be reached, **so that** I can fix it without decoding a stack trace.
  - **Acceptance Criteria:**
    - [x] Given cloud credentials are missing or expired, when the operator launches with Claude selected, then a **clear message** explains the credential problem and how to refresh it — and shows **no stack trace**.
    - [x] Given the account lacks access to the chosen Claude model (or in a required region), when the operator launches, then a clear message **names the access/region problem** and how to resolve it.
    - [x] Given a working model occasionally returns something unusable on a turn, when that happens mid-game, then the game **continues gracefully** (using existing safety nets) rather than crashing.

### 2.6 Nova stays the untouched baseline

- **As an** operator, **I want** Nova to remain available and unchanged, **so that** the proven baseline is always there to fall back to and compare against.
  - **Acceptance Criteria:**
    - [x] With nothing configured (or Nova selected), behavior is **identical to today**.
    - [x] Selecting Claude or Ollama does not change Nova's behavior.

### 2.7 The game plays the same; only the AI's "brain" changes

- **As an** operator, **I want** the rules and flow to be identical regardless of provider, **so that** switching to Claude changes only where the AI thinks, not how the game works.
  - **Acceptance Criteria:**
    - [x] Turn order, voting, the Night/Day structure, win detection, the pre-game career greeting, and the end-of-game career panel behave **identically** whether the provider is Claude, Nova, or Ollama.
    - [x] The **quality and style** of AI dialogue may differ on Claude. This is **expected and not a defect** — there is no guarantee of dialogue quality, consistent with how the game already treats AI output.

---

## 3. Scope and Boundaries

### In-Scope

- A configuration choice to run the game's AI on **Anthropic Claude (Haiku) on the cloud**, selectable alongside Amazon Nova and local Ollama.
- Claude usable for **both local play/evaluation and the hosted (deployed) runtime**.
- **Claude (Haiku) as the default for both tiers**, with each tier's model **independently overridable**, and documented defaults.
- **Single-configuration, no-code switching** among the three providers; **Nova remains the untouched baseline**.
- **Clear, plain-language feedback** when Claude can't be reached (missing/expired credentials, missing model access or wrong region), and graceful continuation on an unusable turn.
- A development approach that **begins with verification ("check/try") spikes** — confirming the real gameplay path reaches Claude **locally and on the deployed runtime**, and that the game's structured outputs round-trip on Claude — *before* building the provider out. (The spikes are detailed in `/awos:tasks`; the deployed spike must prove the runtime can actually reach the model, since that is the historically brittle part.)
- A **documented quickstart** for selecting Claude.

### Out-of-Scope

- **The Opus-4.8 LLM-as-Judge** for game-quality evaluation — a separate concern (Phase 7 — *LLM-as-Judge Game-Quality Evaluation*). This spec is the **gameplay provider** only. (ADR-012 records both selections; only the gameplay provider is specified here.)
- **Switching providers mid-game**, or mixing providers within a single game.
- **Any guarantee of AI dialogue quality** on a given Claude model.
- **Per-tier non-Haiku models as a supported, documented configuration** — overriding a tier to a non-Haiku Claude model (e.g. a Sonnet large tier, per ADR-012's "mixed profile") is *possible* via the override but is **not a documented or verified default** in this spec.
- **Installing or managing cloud credentials / model access on the operator's behalf** — the operator holds the cloud identity; the game only uses it.
- **Changing Amazon Nova or local Ollama behavior** in any way.
- **All other roadmap items** (Phase 6 remaining Personas/Diaries & Phase 6a Async Day Chat / End-of-Game Payoff; Phase 7 Tool-Use, Evidence Citation, Expanded Roles, LLM-as-Judge) — each its own spec.
