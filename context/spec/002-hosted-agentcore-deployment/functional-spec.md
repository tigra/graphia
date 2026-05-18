# Functional Specification: Hosted AgentCore Deployment

- **Roadmap Items:** Phase 2 — Hosted AgentCore Deployment (Bedrock AgentCore Runtime Hosting, AgentCore Gateway-Fronted Tool Surface, AgentCore Memory for Per-Game State, AgentCore Observability, Terraform Provisioning, Local Mode Preserved)
- **Status:** Draft
- **Author:** Poe (on behalf of the project owner)

---

## 1. Overview and Rationale (The "Why")

Phase 2 is Graphia's first piece of cloud infrastructure. The completed Phase 1 skeleton (spec 001) runs entirely on a developer's laptop; Phase 2 makes the same game playable end-to-end against a hosted Bedrock AgentCore runtime in `us-east-1`, while keeping the local laptop path working unchanged. Per ADR 001, the project ships in two parallel run modes — local for game-mechanics development, remote for the AgentCore demonstration — selected via a `--remote` flag at launch.

This spec covers the full hosted-deployment story: the Terraform module that provisions every AgentCore service Graphia needs, the runtime-side application code that serves the game from inside the deployed runtime, the Gateway-fronted diary read/write surface, the AgentCore Memory store backing it, the observability traces flowing to CloudWatch, and the explicit preservation of local mode.

The richer gameplay value of the Memory + Gateway surface — AI players writing creative diary entries that the Moderator dramatizes at end-of-game — does **not** land in Phase 2; that's Phase 6 (Game Feel — Per-AI Private Diaries). Phase 2 establishes the **path** so Phase 6 is just adding content. To prove the path is real, Phase 2 must include a minimal gameplay-time diary write/read round-trip even with placeholder content; without that smoke test, the deployed Memory + Gateway are untested until Phase 6.

**Success looks like:** A new contributor (Curious Dev persona) can clone the repo, run `aws sso login --profile my-aws-profile` and `terraform apply`, then play a full game end-to-end with `uv run python -m graphia --remote` against the deployed AgentCore Runtime in `us-east-1`. They can open CloudWatch Logs afterwards and see structured trace events showing the Runtime served the game and the Gateway-fronted diary surface was exercised. They can run `uv run python -m graphia` (no `--remote`) and play the same game locally with no AgentCore calls. They can run `terraform destroy` and verify all provisioned resources — including AgentCore Memory data — are gone.

---

## 2. Functional Requirements (The "What")

### 2.1 One-time setup and infrastructure provisioning

- **As a developer**, **I want to** provision Graphia's hosted AgentCore stack with one Terraform command, **so that I can** start playing the game against a real hosted runtime without manual cloud configuration.
  - **Acceptance Criteria:**
    - [ ] The repository contains a Terraform module (one root module + supporting files) at a discoverable path.
    - [ ] Running `aws sso login` against the developer's configured SSO profile (project's documented default: `my-aws-profile`) followed by `terraform init` and `terraform apply` provisions all four AgentCore services from a fresh state: AgentCore Runtime, AgentCore Gateway, AgentCore Memory store, and AgentCore Observability wiring.
    - [ ] The Terraform module's region and AWS credentials are configurable inputs (no profile name is hardcoded in source); the project's documented defaults are region `us-east-1` and SSO profile `my-aws-profile`. The module relies on the standard AWS credential chain — typically `AWS_PROFILE` in the environment — rather than embedding a profile name.
    - [ ] After `terraform apply` succeeds, the developer sees a clearly formatted summary at the end of the output naming the deployed Runtime's invocation address (or comparable identifier) and a single-line "next step" hint pointing them to `make play-remote` _(reframed per CR 004 — the repo-root Makefile task-runner is the project's canonical way to launch a remote game)_.
    - [ ] If the developer runs `terraform apply` without an active SSO session (or with an expired token), Terraform fails with an error message that names the missing credentials and tells the developer the exact command to run for their configured SSO profile (e.g., `aws sso login --profile my-aws-profile` for the project's documented default).
    - [ ] All taggable AWS resources are tagged with `Project=Graphia`, `ManagedBy=Terraform`, plus `Environment` and `Owner` tags (values surfaced as Terraform variables).

### 2.2 Launching the game in remote mode

- **As a developer**, **I want to** launch Graphia against the deployed runtime with one extra flag, **so that I can** play the same game I already know but on cloud infrastructure.
  - **Acceptance Criteria:**
    - [ ] Running `uv run python -m graphia --remote` opens the same Textual console interface as local mode.
    - [ ] A persistent `[remote]` badge is visible in a corner of the Textual UI throughout the entire game (welcome screen, Night, Day, end-of-game) so the player always knows they're connected to the hosted runtime. The badge does not obstruct gameplay text.
    - [ ] When local mode is active (no `--remote` flag), the same corner shows a `[local]` badge instead, in the same position and visual style.
    - [ ] _(Revised per CR 004.)_ If the developer launches with `--remote` without an active SSO session (or with an expired token), the AgentCore authentication failure is surfaced to the developer with a clear, actionable error — it need **not** be a dedicated pre-launch refusal with a hardcoded message. Surfacing it through the in-game failure modal (which names the CloudWatch log group and a `thread_id` filter for the failed session) satisfies this criterion.
    - [ ] _(Revised per CR 004.)_ If the developer launches with `--remote` but no AgentCore Runtime is reachable in the configured region (none deployed, or a stale Runtime ARN in `.env`), the failure is surfaced to the developer with a clear, actionable error — it need **not** be a dedicated pre-launch refusal. Surfacing it through the in-game failure modal satisfies this criterion; the deploy path (`infra/terraform/README.md`) documents `make deploy` / `terraform apply` as the remedy.

### 2.3 Playing a game against the hosted runtime

- **As a developer**, **I want to** play a complete game end-to-end against the hosted runtime with the same gameplay as local mode, **so that I can** see the AgentCore deployment actually serve a real session.
  - **Acceptance Criteria:**
    - [ ] All Phase 1 acceptance criteria from spec 001 (Playable Skeleton) hold unchanged in remote mode: the game launches, the Moderator introduces the lineup, the player's role is privately revealed, Nights and Days alternate, vote-to-execute works, win conditions trigger, and the game ends with a decisive ending.
    - [ ] No gameplay-visible behaviour differs between local and remote modes other than the corner badge (§2.2) and the trace destination (§2.5). Roles, lineup, kill resolution, votes, and end-of-game messages are identical.
    - [ ] Game session feel in remote mode is not materially worse than local mode for a player on a normal residential internet connection — first-token latency for AI turns is within the same order of magnitude. 

### 2.4 Per-game memory: diary write/read round-trip through the Gateway

- **As a developer**, **I want to** verify that the deployed Memory store and Gateway-fronted diary surface are actually exercised during gameplay, **so that I can** trust the Phase 2 deployment is fully wired (not just provisioned but never used).
  - **Acceptance Criteria:**
    - [ ] During each Night, every surviving AI player writes a per-game diary entry through the Gateway-fronted MCP surface; the entry is persisted in AgentCore Memory under a per-player namespace scoped to the current game.
    - [ ] On a subsequent turn (Night 2 or later), the AI player's logic reads back its prior diary entries through the same Gateway-fronted surface and the read returns what was written.
    - [ ] Phase 2 diary content is allowed to be a placeholder (e.g., `"diary entry [n]"` with a sequence number) — the rich AI-generated content lands in Phase 6. The acceptance criterion is **path correctness**, not content quality.
    - [ ] At end-of-game, the Memory entries for the just-finished game remain in the store (untouched by the recap, which doesn't read them in Phase 2). They are removed only by `terraform destroy` (§2.7) or by the next deployment's lifecycle.
    - [ ] If the Gateway is unreachable during a write attempt, the game logs the error and falls back gracefully — gameplay continues without that diary write rather than crashing the session. The trace records the failure.
    - [ ] The same gameplay code drives the diary write/read in both modes: in local mode the writes go to in-process LangGraph state; in remote mode they go to AgentCore Memory through the Gateway.

### 2.5 Observability — verifying AgentCore was actually involved

- **As a developer**, **I want to** open CloudWatch after a remote-mode game and see structured traces of what happened, **so that I can** prove the AgentCore Runtime, Gateway, and Memory really served the session and inspect any failures.
  - **Acceptance Criteria:**
    - [ ] After a remote-mode game finishes, the developer can navigate to a CloudWatch log group whose name is exposed as a Terraform output and find structured trace events for the just-finished session, identifiable by a session/game identifier surfaced in the local UI's failure / completion panel.
    - [ ] The trace events include at minimum: Runtime invocation start/finish, each Gateway-fronted diary write and read, the model-invocation roundtrips for AI turns, and the game's win-condition outcome.
    - [ ] _(Added per CR 003.)_ The trace events form a navigable per-session **trace tree** in the AgentCore GenAI Observability console — a nested span hierarchy (Runtime invocation → graph-node execution → per-turn model calls → Gateway tool calls) grouped by the game's session identifier — not a flat list of unparented events.
    - [ ] If a remote-mode game crashes, the CloudWatch traces include a full traceback at the point of failure, and the local Textual UI shows a short failure modal pointing to the CloudWatch log group + filter for the failed session.
    - [ ] Local-mode games do **not** emit anything to CloudWatch — local mode keeps its JSONL trace at `GRAPHIA_LOG_FILE` only.
    - [ ] CloudWatch log retention is set explicitly via Terraform (not "Never expire") to 30 days

### 2.6 Local mode preserved unchanged

- **As a developer**, **I want to** continue running Graphia entirely locally with no AgentCore touches, **so that I can** develop game mechanics without paying for or depending on a deployed runtime.
  - **Acceptance Criteria:**
    - [ ] Running `uv run python -m graphia` (no `--remote`) plays a complete game end-to-end identical to the spec-001 baseline — no AgentCore Runtime invocation, no AgentCore Gateway calls, no AgentCore Memory writes.
    - [ ] Local mode authenticates Bedrock via the standard AWS credential chain — boto3 resolves credentials in the usual way from `AWS_PROFILE` in the environment (the project's documented default profile is `my-aws-profile`; any other SSO profile pointing at a Bedrock-enabled account works the same way). The legacy `AWS_BEARER_TOKEN_BEDROCK` bearer-token path continues to work as a fallback for short-lived workshop tokens, but is no longer the assumed default; it is not required.
    - [ ] Per-AI diaries in local mode (the Phase 2 placeholder version of §2.4) live in the in-process LangGraph state, not in any local file. The `[local]` badge is visible in the corner.
    - [ ] All spec-001 acceptance criteria continue to hold unchanged in local mode.

### 2.7 Tearing down the deployment

- **As a developer**, **I want to** fully remove the deployed AgentCore stack with a single Terraform command, **so that I can** stop incurring any standing infrastructure cost and start fresh whenever I want.
  - **Acceptance Criteria:**
    - [ ] Running `terraform destroy` from the included module removes every resource provisioned by `terraform apply`: Runtime, Gateway, Memory store, Observability wiring, IAM roles/policies, and any supporting resources.
    - [ ] All data stored in AgentCore Memory by Phase 2 — every per-game diary entry written during play — is removed by `terraform destroy`. There are no manual cleanup steps required to wipe Memory data.
    - [ ] After `terraform destroy` succeeds, no Graphia-specific AgentCore resources remain in the AWS account / region the module was deployed to (per the project's documented defaults: account `123456789012`, region `us-east-1` — both configurable via Terraform inputs). Re-running `terraform apply` from a clean state must succeed without naming-conflict errors against the prior deployment.
    - [ ] Local-mode artifacts (`./.graphia/checkpoints/`, JSONL trace logs, the local cross-game stats file once Phase 3 lands) are not touched by `terraform destroy` — they're outside Terraform's scope.

---

## 3. Scope and Boundaries

### In-Scope

- The Terraform module that provisions Runtime, Gateway, Memory, and Observability — region and AWS account are configurable inputs (no profile name hardcoded in source); the project's documented defaults are region `us-east-1`, account `123456789012`, SSO profile `my-aws-profile`.
- The runtime-side application code that serves the game from inside the deployed AgentCore Runtime.
- The Gateway-fronted MCP surface for per-game diary read/write.
- The AgentCore Memory schema and namespacing for per-game diary entries (game-lifetime scope).
- The local AgentCore client that the Textual UI uses to invoke the deployed Runtime in remote mode.
- The `--remote` CLI flag and its associated launch error-handling.
- The persistent `[remote]` / `[local]` corner badge in the Textual UI.
- The CloudWatch trace destination for remote-mode runs.
- Preservation of all spec-001 acceptance criteria in local mode.
- A minimal gameplay-time diary write/read round-trip (placeholder content) sufficient to verify the Memory + Gateway path end-to-end.
- Documented setup steps for a fresh contributor: `aws sso login` + `terraform apply`.
- `terraform destroy` cleanup including AgentCore Memory data.
- Required AWS resource tags (`Project=Graphia`, `ManagedBy=Terraform`, `Environment`, `Owner`).

### Out-of-Scope

- **Long-term cross-game statistics** (career stats, pre-game greeting, post-game career-stats panel) — covered by Phase 3 and a separate spec.
- **Rich AI-generated diary content** — Phase 2 uses placeholder content; creative diary entries land in Phase 6 (AI Personas & Per-Game Memory).
- **AI character sheet generation** — covered by Phase 6.
- **Asynchronous Day chat with rate-limited concurrent AI chatter** — covered by Phase 6.
- **End-of-game creative recap that dramatizes the diaries** — covered by Phase 6.
- **AI tool-use during the Day phase** (investigation, evidence-builder) and **structured Moderator helper tools** — deferred to Phase 7 per CR 002 amendment.
- **AWS Profile / SSO credentials documentation as a user-facing feature** — covered by Phase 4 (AI Provider Flexibility). Phase 2 assumes the developer has the `my-aws-profile` SSO profile already configured (per `project_aws_account` reference).
- **Local Ollama provider** — covered by Phase 4.
- **Configurable role counts** — covered by Phase 5 (Setup Flexibility); Phase 2 uses the spec-001 fixed lineup unchanged.
- **Multi-round Mafia consensus** — covered by Phase 5.
- **Detective / Protector roles, role-mix configuration** — covered by Phase 7.
- **Bedrock Guardrails wiring** — explicitly descoped per CR 001 amendment.
- **Web search / external research tools** — explicitly out of scope per product-definition §3.2.
- **Automated CI/CD pipelines for Terraform** — Phase 2 covers manual `terraform apply` / `destroy` from a developer's laptop; CI/CD is not in scope.
- **Cross-region failover, multi-tenant runtime hosting, or multi-developer shared-deployment concerns** — Phase 2 is single-tenant, single-region, single-developer.
