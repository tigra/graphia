# Functional Specification: Local Ollama Provider

- **Roadmap Item:** Phase 4 — AI Provider Flexibility → **Local Ollama Provider**. (The sibling *AWS Profile / SSO Credentials* sub-item is effectively already shipped and is tracked separately as a roadmap tick / verification, not in this spec.)
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today every game of Graphia reaches its AI through Amazon Bedrock — which needs an AWS account, credentials, and per-token spend. That shuts out two kinds of player: someone with **no cloud access** at all, and someone who simply **doesn't want to pay** (or send anything over the network) just to develop, demo, or play.

This change lets a player run Graphia **entirely against a local model served by [Ollama](https://ollama.com)**, on their own machine. They pick "Ollama" as the AI provider in the game's configuration, point it at a model they're running locally, and play a complete game with **no AWS, no per-token cost, and no internet required**. The game's rules and flow are unchanged — only *where the AI thinks* moves from the cloud to the player's laptop.

**Desired outcome:** a contributor can clone the repo, start a local model, and play a full offline game at zero cost, following a short documented quickstart — without touching AWS.

**Success is measured by:** with Ollama selected and a recommended local model running, a full game (Night → Day → … → end state) completes locally with no cloud calls and no stack traces; and selecting the cloud provider (or configuring nothing) leaves existing behaviour exactly as it is.

---

## 2. Functional Requirements (The "What")

### 2.1 Choose Ollama as the AI provider

- **As a** player without cloud access, **I want** to select a local Ollama model as the game's AI provider through configuration, **so that** I can play without an AWS account or credentials.
  - **Acceptance Criteria:**
    - [ ] The game's configuration offers a way to choose the AI provider, with **Ollama** as an option alongside the existing cloud (Bedrock) default. The choice is made before launch, in the same place the player already chooses between local and cloud-hosted play.
    - [ ] Given Ollama is selected and a local model is running, when the player launches the game in local mode, then the game starts and plays normally **without requiring any AWS credentials**.
    - [ ] Given nothing is configured for the provider, when the player launches, then the game behaves exactly as it does today (cloud / Bedrock) — existing players are unaffected.

### 2.2 A full game runs offline, at zero cost

- **As a** contributor with no cloud budget, **I want** a complete game to run against my local model with nothing sent to the cloud, **so that** it costs nothing and works offline.
  - **Acceptance Criteria:**
    - [ ] Given Ollama is selected with a working local model, when the player plays a full game (Night → Day → … → end state), then the game completes **without reaching any cloud service** and **without requiring internet access**.
    - [ ] All of the game's AI behaviour — AI players' table talk, AI **name generation**, **night-target** selection, and AI **votes** — is produced by the local model.
    - [ ] No per-token cloud charges are incurred for a game played this way.

### 2.3 Two configurable models, with a recommended default

- **As a** player, **I want** to set which local model handles gameplay and which lighter one handles trivial work, with sensible defaults, **so that** I can tune performance but don't have to think about it to get started.
  - **Acceptance Criteria:**
    - [ ] The configuration lets the player set a **gameplay model** (the AI players' dialogue, targeting, and votes) and a separate **lighter model** (AI name generation) **independently**.
    - [ ] Given the player sets neither, when they launch with Ollama selected, then the game uses **documented recommended default models**, so a first-time player can get a game running by following the quickstart.
    - [ ] The documentation names at least one **known-good recommended model** for each tier and the exact steps to make it available locally.

### 2.4 Clear feedback when the local model isn't ready

- **As a** first-time setup, **I want** a clear message if Ollama isn't running or the model isn't available, **so that** I can fix it without reading a stack trace.
  - **Acceptance Criteria:**
    - [ ] Given Ollama is not running (or otherwise unreachable), when the player launches with Ollama selected, then the game shows a **clear, plain-language message** that the local model could not be reached and how to start it — and shows **no stack trace**.
    - [ ] Given the configured model has not been installed locally, when the player launches, then the game shows a clear message **naming the missing model** and how to obtain it.
    - [ ] Given a working model that occasionally returns something unusable on a turn, when that happens mid-game, then the game continues gracefully (using its existing safety nets) rather than crashing.

### 2.5 The game plays the same; only the AI's "brain" changes

- **As a** player, **I want** the rules and flow to be identical regardless of provider, **so that** switching to Ollama changes only where the AI runs, not how the game works.
  - **Acceptance Criteria:**
    - [ ] Turn order, voting, the Night/Day structure, win detection, the pre-game career greeting, and the end-of-game career panel behave **identically** whether the provider is Ollama or cloud.
    - [ ] The **quality and style** of AI dialogue may differ with a local model (typically rougher with smaller models). This is **expected and not a defect** — there is no guarantee of dialogue quality, consistent with how the game already treats AI output.

---

## 3. Scope and Boundaries

### In-Scope

- A configuration choice to run the game's AI against a **local Ollama model** in **local mode**.
- Routing **all** of the game's AI (table talk, name generation, night targeting, votes) to the local model when Ollama is selected.
- **Two independently configurable** local models — a gameplay model and a lighter mechanical model — each with a **documented recommended default**.
- **Clear, plain-language feedback** when Ollama isn't running or the chosen model isn't available, and graceful continuation when a turn's output is unusable.
- A **documented quickstart** for getting a first offline game running with Ollama.

### Out-of-Scope

- **Ollama in remote (cloud-hosted) mode.** Remote play stays on Bedrock — a deployed cloud runtime cannot reach a model running on the player's own machine. The Ollama provider is a **local-play** option only.
- **Installing, updating, or managing Ollama itself, or pulling models on the player's behalf.** The player sets up Ollama and obtains the model; the game only connects to it.
- **Any guarantee of AI dialogue quality** with a given local model.
- **Switching providers mid-game**, or mixing cloud and local AI within a single game.
- The companion **AWS Profile / SSO Credentials** sub-item — effectively already shipped; tracked separately as a roadmap tick / verification.
- **All other roadmap items** (Phase 5 Configurable Role Counts & Multi-Round Mafia Consensus; Phase 6 Personas, Async Day Chat & End-of-Game Payoff; Phase 7 Tool-Use & Expanded Roles) — each its own spec.
