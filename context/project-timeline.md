# Graphia — Project Timeline

A high-level history of the project, reconstructed from `git log` and the
`context/` artifacts: how scope changed (Change Requests), how the architecture
was decided (Architecture Decision Records), and how the work was executed (specs
broken into vertical slices). Covers **2026-04-29 → 2026-06-16**.

Graphia is built with the **AWOS spec-driven workflow** — every increment flows
`product → roadmap → architecture → spec → tech → tasks → implement → verify → tutorial`,
with CRs logging scope shifts and ADRs logging architectural decisions along the way.

---

## Timeline

> **Interactive (clickable) version:** [open the timeline as a live HTML page](https://raw.githack.com/tigra/graphia/main/context/project-timeline.html) — clicking any milestone or slice bar opens its source artifact on GitHub. (GitHub blocks click-through navigation from mermaid diagrams embedded in markdown, so the HTML rendering is the workaround.)

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#2d2d2d', 'textColor': '#e6e6e6', 'sectionBkgColor': '#3c3c3c', 'altSectionBkgColor': '#333333', 'gridColor': '#555555', 'doneTaskBkgColor': '#6aa84f', 'doneTaskBorderColor': '#38761d', 'activeTaskBkgColor': '#e69138', 'activeTaskBorderColor': '#b45f06', 'excludeBkgColor': '#7d5630', 'taskTextColor': '#e6e6e6', 'taskTextLightColor': '#ffffff', 'taskTextDarkColor': '#ffffff', 'taskTextClickableColor': '#9ecbff', 'taskTextOutsideColor': '#e6e6e6'}}}%%
gantt
    title Graphia — CRs, ADRs, and Spec Execution
    dateFormat YYYY-MM-DD
    axisFormat %b %d
    excludes weekends

    section Phase 1 (v1.0)
    Spec 001 — Playable Skeleton · 9 slices             :milestone, done, sp1, 2026-04-29, 0d

    section Rescoping & setup
    CR 001 — AgentCore + AI tool-use into v1.1           :milestone, done, m1, 2026-05-05, 0d
    CR 002 — Long-term Memory in; tool-use to Phase 7    :milestone, done, m2, 2026-05-06, 0d
    ADR 001 — Hosted Runtime + local mode preserved      :milestone, done, a1, 2026-05-07, 0d
    Spec 002 authored · terraform-aws agent hired        :milestone, done, sp2, 2026-05-10, 0d

    section Phase 2 (v1.1) — Spec 002
    ADR 002 — Runtime-embedded Gateway tools             :milestone, crit, a2, 2026-05-12, 0d
    ADR 003 — Bedrock Nova over Claude                   :milestone, done, a3, 2026-05-13, 0d
    ADR 004 — Gateway IAM-auth workaround                :milestone, crit, a4, 2026-05-13, 0d
    ADR 005 — Gateway tools via Lambda targets           :milestone, done, a5, 2026-05-14, 0d
    CR 003 — Observability trace trees                   :milestone, done, c3, 2026-05-15, 0d
    CR 004 — Revise launch criteria                      :milestone, done, c4, 2026-05-18, 0d
    Slices 1-3 — Auth · Terraform · Runtime image        :done, s13, 2026-05-12, 1d
    Slice 3 follow-on — Makefile task-runner             :done, s3f, 2026-05-12, 1d
    Slice 4 — Full remote game · HITL over the wire      :done, s4, 2026-05-13, 1d
    Slices 5-6 — Corner badge · AgentCore Memory diary   :done, s56, 2026-05-13, 1d
    Slice 6 follow-on — DiaryStore factory fix           :done, s6f, 2026-05-13, 1d
    Slice 7 — Gateway tools · ADR 002 to 005 pivot       :done, s7, 2026-05-13, 3d
    Slice 7 follow-on — 5 remote-only bug fixes          :done, fix, 2026-05-15, 1d
    Slice 8 — Observability + remote-mode failure modal  :done, s8, 2026-05-15, 1d
    CR 003 follow-ons — OTEL recipe · IAM · Tx Search    :done, s8f, 2026-05-16, 1d
    Slice 9 — local/remote equivalence tests             :done, s9, 2026-05-18, 1d
    Slice 10 — destroy verification + README             :done, s10, 2026-05-18, 1d
    Slice 11 — Diary round-trip · fallback · deploy hint :done, s11, 2026-05-20, 1d
    Spec 002 verified Completed                          :milestone, done, sp2v, after s11, 0d
    Tutorial 002 published                               :milestone, done, sp2t, after sp2v, 0d

    section Polish & publish (v1.1.x)
    Spec 003 — Reliable game exit (Esc quit modal)       :done, sp3, 2026-05-22, 1d
    Spec 004 — Robust /vote validation + driver fix      :done, sp4, 2026-05-22, 1d
    Tutorial 004 published                               :milestone, done, t4, after sp4, 0d
    Published to GitHub (tigra/graphia) + README         :milestone, done, pub, 2026-05-22, 1d

    section Spec 005 — Play-as-role + determinism posture
    Slices 1-2 — GRAPHIA_ROLE feature + make passthrough :done, sp5s12, 2026-05-23, 1d
    ADR 006 — Test role-pinning via GRAPHIA_ROLE         :milestone, done, a6, 2026-05-23, 0d
    Slices 3-4 — Test-suite migration to GRAPHIA_ROLE    :done, sp5s34, 2026-05-23, 2d
    Slice 5 — Retire GRAPHIA_SEED from production        :done, sp5s5, 2026-05-24, 1d
    Spec 005 verified Completed                          :milestone, done, sp5v, after sp5s5, 0d
    Tutorial 005 published                               :milestone, done, t5, after sp5v, 0d

    section Phase 3 (v1.2) — Spec 006 cross-game career stats
    Spec 006 — Cross-Game Career Stats drafted           :milestone, done, sp6, 2026-05-25, 0d
    Spec 006 tech-considerations                         :milestone, done, sp6t, 2026-05-25, 0d
    ADRs 007 + 008 initial drafts                        :milestone, done, sp6adrs0, 2026-05-25, 0d
    Slices 1-2 — Greeting + win/loss record              :done, sp6s12, 2026-05-28, 1d
    Slices 3-4 — Day-action + night/game counters        :done, sp6s34, 2026-05-28, 1d
    Slice 5 — Abandoned-game on Esc-quit                 :done, sp6s5, 2026-05-28, 1d
    ADR 007 — Two-tier long-term memory stats            :milestone, crit, a7, 2026-05-28, 0d
    Slices 6-7 — Initial remote backend + Terraform      :done, sp6s67, 2026-05-29, 1d
    ADR 008 — Self-managed pipeline (supersedes 007)     :milestone, done, a8, 2026-05-30, 0d
    Slice 8.1-8.9 — Rebuilt pipeline + consumer Lambda   :done, sp6s8, 2026-05-31, 1d
    Architecture doc + Mermaid diagrams                  :milestone, done, sp6arch, 2026-05-31, 0d
    Live-deploy bug parade (4 failure modes)             :done, sp6bugs, 2026-05-31, 1d
    make verify-pipeline — end-to-end harness            :milestone, done, sp6verify, 2026-05-31, 0d

    section Phase 3 close-out + Day-phase integrity (v1.2.x)
    buddah plugin installed — ADR/CR/tutorial skills + hook :milestone, done, bud, 2026-06-03, 0d
    Spec 006 verified Completed                          :milestone, done, sp6v, 2026-06-03, 0d
    Tutorial 006 published                               :milestone, done, sp6tut, 2026-06-03, 0d
    Spec 007 — Fair Day Speaking Order ✓                 :milestone, done, sp7, 2026-06-09, 0d
    Spec 008 — Same-Round Message Visibility ✓           :milestone, done, sp8, 2026-06-09, 0d
    Spec 009 — AI Collusion Awareness ✓ (anti-parrot)    :milestone, done, sp9, 2026-06-10, 0d
    Dialogue-diversity experiment → anti-parrot fix      :milestone, done, divexp, 2026-06-10, 0d
    LLM accessor rename + GraphRecursion flake fix       :milestone, done, fixday, 2026-06-09, 0d
    Tutorials 007–009 published                          :milestone, done, tut789, 2026-06-10, 0d

    section Phase 4 (v1.3) — AI Provider Flexibility
    Spec 010 — Local Ollama Provider (spec + tech)       :milestone, done, sp10, 2026-06-11, 0d
    ADR 009 — Pluggable LLM provider abstraction         :milestone, done, a9, 2026-06-11, 0d
    ADR 010 — Anthropic-compat Ollama protocol           :milestone, done, a10, 2026-06-11, 0d
    Spec 010 implemented — 5 slices + ollama-smoke       :done, sp10impl, 2026-06-11, 1d
    ADR-010 gate — qwen2.5 rejected, qwen3-coder verified :milestone, done, gate, 2026-06-12, 0d
    Follow-ups — offline gate · serde allowlist · fixture rename :milestone, done, fups, 2026-06-12, 0d
    Spec 010 verified Completed — Phase 4 closed         :milestone, done, sp10v, 2026-06-12, 0d
    Tutorial 010 published                               :milestone, done, t10, 2026-06-12, 0d

    section Quality measurement (v1.3.x) — Spec 011 AI Blunder Tracking
    Spec 011 — AI Blunder Tracking (spec + tech + tasks) :milestone, done, sp11, 2026-06-13, 0d
    Day-context fix — Moderator label + whisper privacy  :milestone, done, dcf, 2026-06-13, 0d
    Slices 1-6 — detectors · capture proxy · provenance · Wilson CI :done, sp11impl, 2026-06-13, 1d
    n=20 baseline reverses the n=3 read (repetition)     :milestone, done, base11, 2026-06-13, 0d
    Spec 011 verified Completed (23/23)                  :milestone, done, sp11v, 2026-06-13, 0d
    Tutorial 011 published                               :milestone, done, t11, 2026-06-13, 0d

    section Tooling (v1.3.x) — Spec 012 Eval Ledger Viewer
    Spec 012 — Eval Ledger Viewer (spec + tech + tasks)  :milestone, done, sp12, 2026-06-13, 0d
    Slice 1 — pyyaml + pure data layer + Textual table    :done, sp12s1, 2026-06-13, 1d
    Slices 2-7 — search · drill-down · selector · cell cursor :done, sp12rest, 2026-06-14, 1d
    Spec 012 verified Completed (live walk passed)       :milestone, done, sp12v, 2026-06-14, 0d

    section AI behaviour (v1.3.x) — Spec 013 AI Behavioral Integrity
    Spec 013 — AI Behavioral Integrity (spec+tech+tasks) :milestone, done, sp13, 2026-06-15, 0d
    Slice 1 — outcomes + vote_activity tracking          :done, sp13s1, 2026-06-15, 1d
    Slice 2 — pre-fix baseline n=20/provider (clean)     :milestone, done, sp13b, 2026-06-15, 0d
    Slice 3 — role/team grounding + passivity nudge      :done, sp13s3, 2026-06-15, 1d
    Slice 4 — after-picture (grounding done, passivity refuted) :milestone, done, sp13a, 2026-06-16, 0d
    CR 005 — accept effort-not-results acceptance        :milestone, done, cr5, 2026-06-16, 0d
    Spec 013 verified Completed (under CR 005)           :milestone, done, sp13v, 2026-06-16, 0d

    click sp1 href "https://github.com/tigra/graphia/tree/main/context/spec/001-playable-skeleton"
    click m1 href "https://github.com/tigra/graphia/blob/main/context/change-requests/001-agentcore-and-tools-in-scope.md"
    click m2 href "https://github.com/tigra/graphia/blob/main/context/change-requests/002-long-term-memory-for-cross-game-stats.md"
    click a1 href "https://github.com/tigra/graphia/blob/main/context/adr/001-hosted-agentcore-with-local-mode.md"
    click sp2 href "https://github.com/tigra/graphia/tree/main/context/spec/002-hosted-agentcore-deployment"
    click a2 href "https://github.com/tigra/graphia/blob/main/context/adr/002-runtime-embedded-gateway-tool-handlers.md"
    click a3 href "https://github.com/tigra/graphia/blob/main/context/adr/003-bedrock-nova-over-claude.md"
    click a4 href "https://github.com/tigra/graphia/blob/main/context/adr/004-gateway-target-iam-auth-cli-workaround.md"
    click a5 href "https://github.com/tigra/graphia/blob/main/context/adr/005-gateway-tools-via-lambda-targets.md"
    click c3 href "https://github.com/tigra/graphia/blob/main/context/change-requests/003-observability-navigable-trace-trees.md"
    click c4 href "https://github.com/tigra/graphia/blob/main/context/change-requests/004-revise-launch-error-handling-criteria.md"
    click s13 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s3f href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s4 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s56 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s6f href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s7 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click fix href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s8 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s8f href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s9 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s10 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click s11 href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/tasks.md"
    click sp2v href "https://github.com/tigra/graphia/blob/main/context/spec/002-hosted-agentcore-deployment/functional-spec.md"
    click sp2t href "https://github.com/tigra/graphia/blob/main/context/tutorials/002-hosted-agentcore-deployment/tutorial.md"
    click sp3 href "https://github.com/tigra/graphia/tree/main/context/spec/003-reliable-game-exit"
    click sp4 href "https://github.com/tigra/graphia/tree/main/context/spec/004-robust-vote-input-validation"
    click t4 href "https://github.com/tigra/graphia/blob/main/context/tutorials/004-robust-vote-input-validation/tutorial.md"
    click pub href "https://github.com/tigra/graphia"
    click sp5s12 href "https://github.com/tigra/graphia/tree/main/context/spec/005-play-as-role"
    click a6 href "https://github.com/tigra/graphia/blob/main/context/adr/006-test-role-pinning-via-graphia-role.md"
    click sp5s34 href "https://github.com/tigra/graphia/tree/main/context/spec/005-play-as-role"
    click sp5s5 href "https://github.com/tigra/graphia/tree/main/context/spec/005-play-as-role"
    click sp5v href "https://github.com/tigra/graphia/blob/main/context/spec/005-play-as-role/functional-spec.md"
    click t5 href "https://github.com/tigra/graphia/blob/main/context/tutorials/005-play-as-role/tutorial.md"
    click sp6 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/functional-spec.md"
    click sp6t href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/technical-considerations.md"
    click sp6adrs0 href "https://github.com/tigra/graphia/tree/main/context/adr"
    click sp6s12 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/tasks.md"
    click sp6s34 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/tasks.md"
    click sp6s5 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/tasks.md"
    click a7 href "https://github.com/tigra/graphia/blob/main/context/adr/007-two-tier-long-term-memory-stats.md"
    click sp6s67 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/tasks.md"
    click a8 href "https://github.com/tigra/graphia/blob/main/context/adr/008-self-managed-memory-pipeline.md"
    click sp6s8 href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/tasks.md"
    click sp6arch href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/architecture.md"
    click sp6bugs href "https://github.com/tigra/graphia/commits/main"
    click sp6verify href "https://github.com/tigra/graphia/blob/main/tools/verify_pipeline.py"
    click bud href "https://github.com/tigra/awos/tree/feat/buddah-plugin/plugins/buddah"
    click sp6v href "https://github.com/tigra/graphia/blob/main/context/spec/006-cross-game-career-stats/functional-spec.md"
    click sp6tut href "https://github.com/tigra/graphia/blob/main/context/tutorials/006-cross-game-career-stats/tutorial.md"
    click sp7 href "https://github.com/tigra/graphia/tree/main/context/spec/007-fair-day-speaking-order"
    click sp8 href "https://github.com/tigra/graphia/tree/main/context/spec/008-same-round-message-visibility"
    click sp9 href "https://github.com/tigra/graphia/tree/main/context/spec/009-ai-collusion-awareness"
    click divexp href "https://github.com/tigra/graphia/blob/main/context/spec/009-ai-collusion-awareness/repetition-experiment-design.md"
    click fixday href "https://github.com/tigra/graphia/commits/main"
    click tut789 href "https://github.com/tigra/graphia/blob/main/context/tutorials/009-ai-collusion-awareness/tutorial.md"
    click sp10 href "https://github.com/tigra/graphia/tree/main/context/spec/010-local-ollama-provider"
    click a9 href "https://github.com/tigra/graphia/blob/main/context/adr/009-pluggable-llm-provider-abstraction.md"
    click a10 href "https://github.com/tigra/graphia/blob/main/context/adr/010-anthropic-compatible-ollama-protocol.md"
    click sp10impl href "https://github.com/tigra/graphia/blob/main/context/spec/010-local-ollama-provider/tasks.md"
    click gate href "https://github.com/tigra/graphia/blob/main/context/spec/010-local-ollama-provider/tasks.md"
    click fups href "https://github.com/tigra/graphia/blob/main/context/spec/010-local-ollama-provider/tasks.md"
    click sp10v href "https://github.com/tigra/graphia/blob/main/context/spec/010-local-ollama-provider/functional-spec.md"
    click t10 href "https://github.com/tigra/graphia/blob/main/context/tutorials/010-local-ollama-provider/tutorial.md"
    click sp11 href "https://github.com/tigra/graphia/tree/main/context/spec/011-ai-blunder-tracking"
    click sp11impl href "https://github.com/tigra/graphia/blob/main/context/spec/011-ai-blunder-tracking/tasks.md"
    click base11 href "https://github.com/tigra/graphia/blob/main/evals/blunder-ledger.yaml"
    click sp11v href "https://github.com/tigra/graphia/blob/main/context/spec/011-ai-blunder-tracking/functional-spec.md"
    click t11 href "https://github.com/tigra/graphia/blob/main/context/tutorials/011-ai-blunder-tracking/tutorial.md"
    click sp12 href "https://github.com/tigra/graphia/tree/main/context/spec/012-eval-ledger-viewer"
    click sp12s1 href "https://github.com/tigra/graphia/blob/main/context/spec/012-eval-ledger-viewer/tasks.md"
    click sp13 href "https://github.com/tigra/graphia/tree/main/context/spec/013-ai-behavioral-integrity"
    click sp13s1 href "https://github.com/tigra/graphia/blob/main/context/spec/013-ai-behavioral-integrity/tasks.md"
    click sp13b href "https://github.com/tigra/graphia/blob/main/evals/blunder-ledger.yaml"
    click sp13a href "https://github.com/tigra/graphia/blob/main/evals/blunder-ledger.yaml"
    click cr5 href "https://github.com/tigra/graphia/blob/main/context/change-requests/005-ai-behaviour-acceptance-effort-not-results.md"
    click sp13v href "https://github.com/tigra/graphia/blob/main/context/spec/013-ai-behavioral-integrity/functional-spec.md"
```

**How to read it.** Each visual channel encodes exactly one thing:

- **Colour = status.** Green = completed / accepted / in effect · Red = superseded · Orange = pending or in-flight.
- **Shape = kind.** Diamonds are point events (CRs, ADRs, spec milestones); bars are executed slice work spanning real days.
- **Sections = project phase.** ADRs are listed first within Phase 2, then the slice bars, so a superseded ADR (red diamond) is never mistaken for blocked work.

The red marks are the three superseded ADRs (002, 004, 007); the CRs are green because — even though CR 001 and 002 carried `Proposed` for a while — the scope changes were fully executed and have now been formally Accepted. The Phase 3 section now shows the full implementation arc — Slices 1-8 plus the ADR 007 → 008 mid-stream pivot plus a four-bug live-deploy parade ending at the `verify-pipeline` harness. Spec 006 has since been **verified Completed** and **Tutorial 006** published; the close-out section's three Day-phase integrity specs (007 fair order, 008 visibility, 009 collusion awareness) are now all **Completed** (green) — 009 via an experiment-chosen anti-parrot reword — alongside an LLM-accessor rename and a recursion-flake fix.

---

## What was going on — thirteen acts

### Act 1 — Phase 1: a playable skeleton (2026-04-29)

The project began as a complete, end-to-end console Mafia game: a fixed 7-player
lineup, Night→Day phase alternation, single-round kill/execute voting, and
human-in-the-loop turns. This was **Spec 001 — Playable Skeleton** (9 vertical
slices), and it landed as the initial commit — proving the core LangGraph loop
worked before any flexibility or cloud deployment was layered on.

### Act 2 — Rescoping: two Change Requests reshape v1 (2026-05-05 → 05-06)

Two CRs, logged a day apart, redefined what v1 means:

- **CR 001** promoted **Bedrock AgentCore deployment** from an optional future
  item to a **hard v1.1 requirement** — Graphia must demonstrate AgentCore as a
  real production deployment target. It also first introduced AI tool-use as a
  v1.1 feature.
- **CR 002** (next day) added a second AgentCore use-pattern — **long-term,
  cross-game Memory** for career stats — as hard v1.2 scope, and **demoted AI
  tool-use to Phase 7**. The demotion follows Graphia's *design-driven-by-realistic-needs*
  principle: a feature earns a slot only when the game genuinely needs it.

Net result — the roadmap was restructured: **Phase 2** = Hosted AgentCore
Deployment, **Phase 3** = Long-Term Cross-Game Memory, **Phase 7** = AI Tool-Use.
Between the CRs and the first Phase 2 code, the spec was authored and a
specialist **`terraform-aws` agent was hired** (2026-05-10) to own the IaC.

### Act 3 — Phase 2: hosting Graphia on AgentCore (2026-05-12 → 05-20)

**Spec 002 — Hosted AgentCore Deployment** shipped slice by slice over nine days:

- **ADR 001** set the foundational shape: run the same LangGraph topology in two
  modes — a hosted AgentCore Runtime *and* a no-AWS local mode.
- **Slices 1-3** (05-12) delivered the config/auth refactor (AWS credential chain
  replaces the bearer token), the Terraform module skeleton, and the Runtime
  container image (~330 MB, multi-stage).
- **Slice 3 follow-on** (05-12) turned the Makefile into the project task-runner
  and added an ECR force-delete safeguard — work surfaced by the first real
  deploy/destroy cycle.
- **Slice 4** (05-13) was the headline: a full game played end-to-end against the
  hosted Runtime, with `interrupt()` / `Command(resume=…)` HITL turns
  round-tripped over the wire. A subtle bug — AgentCore routing by an unstable
  session id — caused an infinite "enter your name" loop until session ids were
  pinned via `uuid5`.
- **ADR 003** (05-13) swapped the model family — forced, not chosen: every viable
  Anthropic Claude model on Bedrock was end-of-life, inference-profile-only, or
  too small, and the `us.*` cross-region profile fanned out to regions where the
  role couldn't auto-subscribe. **Amazon Nova Pro + Lite** invoke directly in
  `us-east-1` with no profile.
- **Slices 5-6** (05-13) added the `[local]`/`[remote]` UI badge and the
  AgentCore Memory-backed diary store behind a `DiaryStore` Protocol.
- **Slice 6 follow-on** (05-13) fixed a silent bug a USER smoke test caught — the
  diary-store factory gated on `remote_mode` instead of `memory_id`, so the
  Runtime fell back to an in-process store and diary writes vanished when the
  microVM cycled.
- **Slice 7** (05-13 → 05-15) was the hard one — see below.
- **Slice 8** (05-15) wired AgentCore Observability and the remote-mode failure
  modal: structured trace events, a 30-day CloudWatch retention policy, and a UI
  surface so a remote-only error reaches the human instead of dying silently.
- **CR 003** (05-15) — written the same day as Slice 8 landed — sharpened the
  observability acceptance criterion from "structured logs exist" to "a navigable
  per-session trace tree exists in CloudWatch Transaction Search". The next day
  (05-16) the trace tree was still flat, which forced three follow-on fixes: the
  Runtime's OpenTelemetry / OpenInference LangChain instrumentation recipe was
  corrected, the execution role gained the missing observability permissions
  (the real root cause), and the Transaction Search log resource policy was
  brought under Terraform management.
- **Slice 9** (05-18) added the local↔remote equivalence test suite — same
  initial state played through both drivers produces the same node sequence and
  end-game state, guarding against silent mode drift.
- **Slice 10** (05-18) closed teardown: a `terraform destroy` cycle verified
  against live AWS state, an `aws-inventory` harness that lists every Graphia
  resource by tag, and a README walkthrough for the deploy/destroy loop.
- **CR 004** (05-18) was authored *by* the first `/awos:verify` pass — three
  §2.1/§2.2 acceptance criteria described error behaviour the delivered design
  intentionally handled differently (collapsed config errors, hint-driven setup
  flow), and §2.4.2 promised a diary write/read **round-trip** but only the
  write half was wired. CR 004 revised the launch criteria *and* planned Slice
  11 to close the §2.4.2 gap.
- **Slice 11** (05-20) added the gameplay-time diary read-back, a graceful
  Memory fallback so a transient Memory outage degrades instead of crashing, and
  a deploy-hint banner that prints the exact next command when remote config is
  missing. With Slice 11 in, **`/awos:verify` flipped Spec 002 to Completed** the
  same day, and **Tutorial 002** — a single depth-first walkthrough covering all
  eleven slices — was published. **107/107 tests pass**, **85/85 task items
  done**, Phase 2 closed.

### Act 4 — Polish, hardening, and going public (2026-05-20 → 05-22)

With Phase 2 closed, two small specs and a public release followed:

- **Spec 003 — Reliable Game Exit Controls** (05-22): `Esc` opens a quit-confirm
  modal; `q` is deliberately left unbound so words starting with "q" can be typed
  in day chat; `Ctrl+C` still force-quits. Fixed an "Esc closes the UI but the
  process hangs" defect — the LangGraph stream thread could be parked mid-Bedrock
  call, so a cancel-pending-future plus a 0.5s daemon `os._exit` fallback
  guarantees a clean exit. Verified Completed.
- **Spec 004 — Robust /vote Input Validation** (05-22): strict slash-command
  parsing (`/voted`, `/votefor` are speech; bare `/vote` shows a usage hint) plus
  a real bug fix — a bad `/vote` *ended the game* because the re-prompt called
  `interrupt()` twice in one node execution, and the driver returns on an empty
  `snapshot.next` before it inspects pending interrupts. Restructured to one
  interrupt per node, re-prompting via a `day_turn_error` state channel and a
  conditional-edge loop; a driver-level regression test (running the real
  `drive_graph`, not a hand-driven `graph.stream`) locks it. Verified Completed;
  **Tutorial 004** published. (No Tutorial 003 — that slot is intentionally left
  open.)
- **Going public** (05-22): the repo was published to
  **github.com/tigra/graphia** with a README (mermaid architecture diagram,
  make-first workflow, AWOS-extension links). Tooling was hardened in passing —
  the Makefile auto-loads `.env`, derives the AWS account from the active profile,
  runs a safe two-step `terraform destroy`, and `make wire-env` now discovers
  deployed resources via the AWS API (no Terraform state needed).

### Act 5 — Determinism posture: GRAPHIA_ROLE feature and the seed retirement (2026-05-23 → 05-24)

**Spec 005 — Play-As-Role via Environment Variable** opened as a small developer
affordance — a `GRAPHIA_ROLE` env var to pin the human's side at launch so the
author could exercise Mafia-only or Law-abiding-only flows without relaunching
until the random deal cooperated. But drafting it exposed a much bigger
question: the test suite was already pinning roles indirectly, via `GRAPHIA_SEED`
magic values like `SEED_MAFIA = 3` and `SEED_LAW_ABIDING = 0` that incidentally
dealt the desired side. That pattern was opaque (the seed value's effect needed
a constant lookup to understand), fragile (any refactor of `assign_roles` would
silently break the seed-→role mapping), and — once `GRAPHIA_ROLE` arrived —
obviously redundant. The spec grew across five slices:

- **Slices 1-2** (05-23): the user-facing feature. `GRAPHIA_ROLE` parsed in
  `GraphiaConfig.load_config()`, applied inside `assign_roles` via a
  **pop-then-shuffle** strategy that preserves the 2-Mafia / 5-Law-abiding
  composition by construction, surfaced via a `make play ROLE=mafia` Makefile
  passthrough, and validated at startup (in `__main__.py`) so invalid values
  raise `SystemExit` on stderr before Textual takes the alternate screen.
- **ADR 006 — Test role-pinning convention** (05-23): captured the architectural
  decision — `GRAPHIA_ROLE` setenv is the role-pinning mechanism in tests;
  magic-seed-for-role is retired. The alternatives (status quo vs the chosen
  convention) and their trade-offs are recorded in the ADR.
- **Slice 3** (05-23): migrated ~33 call sites across 10 test files from
  magic-seed-for-role to `monkeypatch.setenv("GRAPHIA_ROLE", "<role>")`. Several
  sites turned out to have a hidden second dependency on the seed value beyond
  role-pinning (typically a specific day-speech order), and those sites kept the
  seed with a renamed descriptive constant — `SEED_HUMAN_MID_DAY_ORDER`,
  `SEED_DAY1_SPEAKER_ORDER_LETS_AI_INITIATE_VOTE`, etc. — pending Slice 4.
- **Slice 4** (05-23 → 05-24): refactored the 5 remaining seed-dependent test
  sites to monkeypatch the production helper directly (`monkeypatch.setattr(
  graphia.nodes.day, "_shuffle_order", <stub>)`) rather than nudging via
  `GRAPHIA_SEED`. The renamed-descriptive constants disappeared along with their
  setenvs. Architecture.md gained §6 "Determinism Posture & Testing Conventions"
  codifying the three principles: LLM outputs accepted as variable; direct intent
  expression over fragile mechanisms; mechanical-RNG decisions pinned via
  targeted monkeypatching.
- **Slice 5** (05-24): the cleanup arc. With the testing convention in place, a
  grep across the codebase found two surviving `GRAPHIA_SEED` consumers: the
  seed-→role mapping test itself (whose subject was now gone — file deleted) and
  the dual-mode cross-mode byte-equality test in `tests/test_dual_mode_smoke.py`
  (a real regression-guard worth preserving). The byte-equality test moved its
  determinism mechanism into the test body via `random.seed(...)`, and the seed
  was retired from production entirely: `GraphiaConfig.seed`, `GRAPHIA_SEED`
  parsing, and the per-call salt arithmetic (`config.seed + cycle * 1009`-style)
  in `day.py` / `night.py` / `setup.py` are all gone. Production RNG uses
  module-global `random.shuffle` / `random.choice`. ADR-006 was amended to
  cover the production-side retirement and to record why we kept the byte-equality
  test rather than downgrading it to structural equality (the alternative we
  weren't happy with).

Verified Completed (29/29 acceptance criteria); **Tutorial 005** published with
a depth-first Socratic walkthrough of the determinism posture as the conceptual
spine. The full test suite ended at **129 passed, 1 skipped** (down from 132 —
three deletions: the seed-→role mapping test, the unset-path frozen-list
regression test, and the cross-parametrize identity test refactored to a
parsing-layer assertion that needs no RNG). Zero `GRAPHIA_SEED` hits anywhere in
the repo's `*.py` files.

### Act 6 — Phase 3 kickoff: cross-game career stats (2026-05-25)

With Phase 2 closed and the determinism posture codified, Phase 3 — the project
roadmap's headline cross-session AgentCore Memory demonstration — opened with
a complete planning bundle:

- **Spec 006 — Cross-Game Career Stats** (Draft): a persistent career layer for
  the human player — counters for games played, wins by role, day-vote
  initiations, day-ballots cast, and mafia-pointing attempted-vs-successful
  kills, persisted across game sessions. The player sees a one-paragraph
  career-summary greeting on launch, and a post-game stats panel with deltas
  after the Moderator's recap. Abandoned-via-quit games tracked separately.
- **ADR 007 — Cross-Game Stats as Self-Authored AgentCore Long-Term Memory
  Records** (Proposed, pending review): picks the AgentCore Memory mechanism.
  Argues against built-in `SEMANTIC` / `SUMMARIZATION` strategies (LLM
  extraction can't preserve exact integer counters) and against short-term
  events as a rolling aggregate (not the long-term-Memory feature CR 002
  promised). Picks a self-managed (custom) strategy with self-authored records
  via the batch-record APIs, read deterministically by namespace. Also
  corrects a wording bug in architecture.md §2 and ADR 001 that conflated
  "long-term scope" with "long-lived data".
- **ADR 008 — Client-Owned Cross-Game Stats via Running-Total `GameState`
  Counters** (Proposed, pending review): picks the ownership seam. Store I/O
  lives in the UI/client layer (`ui/app.py`), not in graph nodes, keeping the
  LangGraph topology mode-agnostic per ADR 001. Graph nodes maintain
  running-total counters in `GameState` (replace semantics) so the latest
  state snapshot is the authoritative end-of-game source — no need to
  aggregate deltas from the `stream_mode="updates"` consumer.
- **Architecture.md §2** received a small amendment to name ADR 007's
  mechanism explicitly (self-managed long-term Memory strategy + batch-record
  APIs + namespace-deterministic reads).

The artifacts arrived as a parallel-session branch (`claude/status-check-O6zuB`)
that was rebased onto current `main` and fast-forwarded in; both ADRs were
downgraded from `Accepted` to `Proposed` to surface them for deliberate review
before binding the implementation. Tasks breakdown (`/awos:tasks 006`) and
slice-by-slice implementation remain ahead.

### Act 7 — Phase 3 implementation, twice (2026-05-28 → 05-31)

The Act 6 ADRs were reviewed and **ADR 007 was Accepted in revised form —
"Two-tier long-term memory stats"** (05-28): exact integer records via the
batch-record APIs now, with a future "semantic" tier left as an opening for
a later spec. The original ADR 008 framing — client-owned stats via
running-total `GameState` counters — was effectively absorbed; the
running-total approach landed inside the slices below as the natural shape
for the local store, without needing its own architectural binding.

**Slices 1-5 (05-28)** built the full career-stats feature in local mode in
a single day. Slice 1 introduced the store seam — `stats_store.py`,
`render_greeting`, the first-run welcome line, and `LocalFileStatsStore`.
Slice 2 added `fold` / `summarize` and the post-game panel. Slices 3-4
added day-action (votes called, ballots cast) and night/game-wide counters
(kills attempted/successful, day executions, night victims). Slice 5 wired
the Esc-quit path so an abandoned game is recorded as an "abandoned"
outcome — counted in games-played but excluded from win-rate denominators.
With Slice 5 in, local mode was feature-complete; `.graphia/career.json`
accumulated across launches.

**Slices 6-7 (05-29)** delivered the first remote backend per ADR 007:
Slice 6 added the `career_memory_id` / `stats_strategy_id` config seam and
an `AgentCoreLongTermStatsStore` that wrote records directly via
`batch_create_memory_records`; Slice 7 added Terraform for a dedicated
AgentCore Memory plus an out-of-band CLI step (`make
create-stats-strategy`) to attach a `SELF_MANAGED` strategy — because the
`hashicorp/aws` provider only supports `CUSTOM` (LLM-extraction) strategies,
not self-managed ones.

The **Slice 7 post-deploy reveal** was that the strategy's S3 + SNS +
Lambda scaffolding sat *unused*: the direct-write store wrote records
without ever producing events, so the `SELF_MANAGED` strategy never had
anything to fire on. The architecture was paying for scaffolding it didn't
exercise. **ADR 008 was rewritten as "Self-managed memory pipeline"
(05-30)** and formally **superseded ADR 007**: per-action events flow to
AgentCore's short-term tier, the strategy's S3 + SNS deliveries trigger a
consumer Lambda, and the Lambda materialises consolidated long-term records
by folding the session's events. The intended AWS pattern, end to end,
instead of half-using it.

**Slice 8 (05-31)** rebuilt the remote pipeline against ADR 008 across nine
sub-slices: a dedicated career Memory with the scaffolding re-attached
(8.1), the `career_memory_id` config seam (8.2), the shared `career_events`
module (8.3), per-action emissions from six graph nodes via a
`partial`-wrapper service-injection pattern (8.4), `game_abandoned`
emission on Esc-quit (8.5), the read-only `AgentCoreCareerEventStore` that
replaced the direct-write store (8.6), the consumer Lambda with
`games_folded` session-id idempotency (8.7), Terraform wiring for the
consumer Lambda (8.8), and a fresh pipeline-equivalence test suite (8.9).
An **architecture document with eight embedded mermaid diagrams** joined
the spec dir to map the new shape.

**Then the live deploy exposed four distinct bugs, none caught by the
all-mocked test suite:**

1. **Runtime IAM missing `bedrock-agentcore:CreateEvent`** on the career
   Memory — the emitter `create_event` calls were silently being swallowed
   in remote mode. Fix: grant the permission *and* remove the silent
   try/except in the emitter so future IAM gaps fail loud.
2. **`build_runtime_graph` parity gap** — Slice 8.4 plumbed the
   career-emitter into local-mode `build_graph` but missed the runtime-side
   builder (the two graphs were hand-mirrored copies, with the docstring
   "*mirror this here if you change the local graph*" — the discipline
   failed exactly as it always does). Two-step fix: wire the emitter
   through the runtime builder, then **refactor both builders to share a
   single `_assemble_graph` helper** so the drift class is structurally
   impossible going forward.
3. **`includePayload` (singular) typo** in the consumer Lambda's
   `list_events` kwargs — local tests stubbed `list_events(**kwargs)`
   without inspecting kwargs, so the boto3 `ParamValidationError` only
   surfaced against real boto3.
4. **`batch_create_memory_records` missing on the Lambda runtime's boto3**
   — the Python 3.13 Lambda runtime ships an older snapshot that has
   `list_events` and `list_memory_records` but not the batch record-write
   methods. Local boto3 has them, so unit tests passed; the Lambda crashed
   with `AttributeError` at the write site. Vendored a current boto3 in
   `requirements.txt` so the zip carries its own copy.

In parallel, **`record(summary)` was simplified to return `self.load()`
unchanged** instead of an in-process `fold(load(), summary)` synthesis. The
fold had been hiding the boto3 bugs — the post-game panel always showed a
happy "+1 this game" even when nothing was persisted, masking a broken
pipeline as a cosmetic delay. The new behaviour shows the actually-
materialised state; if the async write hasn't landed yet, the panel
honestly shows pre-game numbers, and the next session's greeting reflects
the delta only once the consumer Lambda completes (typically a 2-3 min
async lag at the strategy's default trigger settings).

To stop the post-mortem-by-CLI-call cycle, **two new test modules locked the
boto3 contract at unit-test time** —
`tests/test_boto3_api_contract.py` walks
`boto3.client('bedrock-agentcore').meta.service_model` to validate every
operation name and parameter name the codebase calls, and
`tests/test_lambda_zip_contents.py` opens the built `career_consumer.zip`
and asserts vendored `boto3` / `botocore` packages plus a gzipped
bedrock-agentcore service description that exposes the batch-record
operations. Together they cover the typo class (1, 3) and the runtime-
version-mismatch class (4) at `pytest -q` time, against artifacts that
mirror what the Lambda actually loads.

And **`make verify-pipeline`** went into the Makefile — a six-stage
read-only harness against the live deploy that asserts the runtime image
tag matches HEAD, `.env` carries the career memory id, the `human-career`
actor exists in career memory, the self-managed strategy is `ACTIVE`, the
Lambda's latest log stream has no `[ERROR]` / `ParamValidationError` /
`AttributeError` markers, and the TUI's exact
`make_stats_store(load_config()).load()` returns the same record AgentCore
has. First run against the `eafa1ee` deploy: all six green, with the most
recent game's session id in `games_folded` — end-to-end proof, not promise.

Spec 006 ends Phase 3 implementation at **42/42 tasks `[x]`** and **232
passing tests** (1 skipped, 1 known flake — `test_vote_validation` passes
in isolation, fires ~1-in-3 in the full suite under
`GraphRecursionError`). The functional spec stays `Draft` only because
`/awos:verify 006` and `/awos:tutorial 006` are the deliberate next steps,
not because anything is unfinished.

### Act 8 — Phase 3 closed out, plus a Day-phase integrity trio (2026-06-03)

Spec 006's deliberate next steps were taken. **`/awos:verify 006`** checked all
32 acceptance criteria against the delivered state and flipped the functional
spec and technical-considerations to **Completed**, with Phase 3's five roadmap
bullets ticked (the lone `test_vote_validation` flake reproduced — passing in
isolation, a pre-existing determinism artifact, not a 006 regression).
**Tutorial 006** was then published as a depth-first walkthrough of the
self-managed Memory pipeline — opening with a candid note that the whole
two-tier pipeline is *deliberately over-engineered* for a stats counter: a
technology demonstration, not a need-driven design, and an explicit sidestep of
the project's design-driven-by-realistic-needs principle (the plain-file local
store is all the feature actually needs).

A close read of the Day speaking loop then produced three small **Day-phase
integrity specs**, all `Draft`:

- **Spec 007 — Fair Day Speaking Order**: speaking order must be provably
  independent of role and of human/AI player type, enforced by structural +
  statistical tests. (Confirmed in passing that today's `_shuffle_order` is
  already a uniform, role-blind shuffle — the spec locks it in.)
- **Spec 008 — Same-Round Message Visibility** (+ technical-considerations):
  speakers must see the current round's earlier messages; the recent-discussion
  window widens 10 → 30 so a full round fits, verified by a chokepoint unit test
  plus one UI test. The human side needs no code change — the on-screen log was
  never windowed; `_CONTEXT_WINDOW` only ever bounded the AI prompt feed.
- **Spec 009 — AI Collusion Awareness**: a light prompt nudge that copycat
  messages may signal collusion; behaviour left emergent, no automated test.

The session also exercised the **buddah plugin** — the AWOS marketplace
packaging of the `/adr`, `/change-request`, `/tutorial` skills plus a proactive
`awos-next` suggestion hook. Installing it surfaced a real plugin bug: the
`UserPromptSubmit` hook used `${CLAUDE_PLUGIN_DIR}`, an unset variable that
collapsed the command path to `/hooks/…` and failed every prompt (every
*official* plugin uses `${CLAUDE_PLUGIN_ROOT}`). Pointing the cached copy at the
real path fixed it — proven from the session transcript, where the hook
attachment flipped from `hook_non_blocking_error` to `hook_additional_context`
the instant the path was corrected. The fork-side fix (the variable name, plus
broadening the matcher and suggestion text to the `/buddah:*` namespace) remains
open.

Two commits landed and were pushed: an **AWOS-tooling commit** (re-synced
framework command files + buddah enablement) and a **project-work commit** (006
verification + Tutorial 006 + specs 007–009). The `/adr` · `/change-request` ·
`/tutorial` → `_`-prefixed command renames are intentionally left uncommitted
for now.

---

### Act 9 — Closing the Day-phase trio, and an experiment to fix what it broke (2026-06-09 → 06-10)

The integrity trio was **finished**: Spec 008 verified Completed with a tutorial,
and Spec 007 likewise (its `_shuffle_order` was already fair; the suite locks it
in). Spec 009 was then implemented and verified — a one-line Day-speak nudge that
copycat messages may signal collusion.

**The recursion flake, finally root-caused.** The intermittent
`GraphRecursionError` that had haunted the vote tests for weeks turned out *not*
to be the "unpinned Night-RNG tail" an earlier `eb51582` "deflake" had guessed
at. The real cause: the tests' Night-pointing override read `graph.get_state()`
**re-entrantly mid-stream**, which returns a **stale pre-`assign_roles`
snapshot** (every player still `law_abiding`). So the override named the first
AI — sometimes actually Mafia — `_ai_pick_target` rejected it and fell back to
`random.choice(alive_law_abiding)`, a set that **includes the human**; the
night-killed human then stopped interrupting and the Night→Day drive free-ran
past `recursion_limit=50`. The fix derives the target from the **prompt's own
roster** instead of live state, across all the affected tests, and retires a
now-vestigial `random.seed` band-aid — verified 0 failures over 132 stress runs.
A candid correction: the earlier deflake had been aimed at the wrong line.

**A misnomer corrected.** `get_sonnet`/`get_haiku` were bound to **Amazon Nova
Pro / Lite** (per ADR-003) — and CLAUDE.md still claimed "Sonnet 4.5 / Haiku 4.5
/ eu-north-1." Renamed to capability-tier `get_large`/`get_small` (model-agnostic
so the next swap needs no rename) and the docs corrected. There is no Claude in
the gameplay path, local or remote.

**The repetition spiral — and an experiment to settle it.** Remote play surfaced
the opposite of Spec 009's intent: instead of making copycatting *rarer*, the
collusion nudge primed Nova to **echo and obsess over repeated phrasing**, Day
chat collapsing into players accusing each other of repetition in repeated
phrases. CloudWatch trace archaeology pulled the actual speeches (and confirmed
the Nova-Pro model), and a new **`make eval-dialogue`** harness quantified it
(~77% distinct, matching the live session). A quick n=2 A/B was *too noisy to
trust* (a same-config replication swung 33% → 47%), so the work pivoted to a
**rigorous experiment** ([design](../spec/009-ai-collusion-awareness/repetition-experiment-design.md)):
9 conditions × N=10 paired games on real Nova, length-capped, name-masked
similarity, bootstrap CIs, paired-vs-HEAD with Holm — runnable via
**`make repetition-experiment`**. The result **reversed the pilot**: the
anti-parrot reword the noisy A/B had called a *failure* was in fact the best
design-preserving fix — name-masked near-dup **0.57 → 0.15, below the pre-spec
baseline (0.20)** — while keeping Spec 008's full context window. A secondary
lesson: *removing* the collusion line alone barely helped (−0.13); you must
**instruct against** parroting, not merely stop priming it. The fix was adopted
in place (no CR — 009 was still being iterated), with the spec carrying a
revision note.

A final gotcha closing out a `make redeploy`: a SigV4 **`Signature expired`**
error that was neither creds nor the host clock but the **Podman VM clock**,
drifted ~16 minutes behind after a laptop sleep — fixed by syncing the VM clock
to the host.

Several project-work commits landed and were pushed across the two days (008/007
verification + tutorials, the flake fix, the model rename, 009 implement/verify,
and the experiment + anti-parrot fix). The `fake_sonnet`/`fake_haiku` *fixture*
rename (the same misnomer, ~100 test refs) and the AWOS `_`-prefixed command
renames remain intentionally uncommitted.

---

### Act 10 — Phase 4: a pluggable LLM provider, offline play, and the gate that fired twice (2026-06-11 → 06-12)

**Phase 4 — AI Provider Flexibility** opened. The literal next roadmap checklist
item (*AWS Profile / SSO Credentials*) turned out to be **effectively already
shipped** — SSO has been the canonical Bedrock auth path since May — so the real
target is the **Local Ollama Provider**: play a full game **offline, at zero
cost**, against a local model. **Spec 010** was drafted (choose Ollama in config;
a complete offline game; two independently configurable models with a documented
default; plain-language errors when Ollama isn't ready; identical mechanics,
model-dependent quality) plus its technical-considerations.

The tech work surfaced **two architectural decisions**, each logged as an ADR —
and the interview itself was sharpened twice by the author:

- **ADR 009 (Accepted) — pluggable LLM-provider abstraction.** A normal abstract
  provider interface with a Bedrock implementation and an Ollama implementation,
  selected by a branch in the existing `get_large`/`get_small` factory — chosen
  over a heavier self-registering *registry* (over-engineered for two providers)
  and a bare *inline branch* (no clean separation). Blast radius is tiny (every
  call site already goes through that one seam), and it makes local mode **fully
  offline** for the first time — which revises the architecture doc's "local mode
  hits AWS only for Bedrock."
- **ADR 010 (Accepted) — Anthropic-compatible protocol for Ollama.** The Ollama
  implementation talks to Ollama's **Anthropic Messages–compatible `/v1/messages`**
  endpoint (via `langchain-anthropic`, local `base_url`) rather than the native
  or OpenAI-compatible surfaces. The rationale is strategic: Anthropic was
  Graphia's **original** intended model family, and Nova-on-Bedrock (**ADR 003**)
  was a deliberate cost detour — so an Anthropic client *locally* keeps a path to
  future single-client unification if the project ever returns from Nova to
  Claude. The load-bearing risk — whether tool-use / structured output works over
  Ollama's newest compat surface — is held behind an **implementation smoke-test
  gate** with native-`ChatOllama` / OpenAI-compat fallbacks (cheap to swap thanks
  to the ADR-009 abstraction).

Two author corrections shaped the records: that the **protocol is a separate
dimension** from the abstraction (so it became its *own* ADR rather than a
rejected alternative), and that the Anthropic-compatible endpoint should be
**verified, not assumed** — a quick docs check confirmed Ollama really does serve
`/v1/messages`. The tech spec was then **reconciled** to both ADRs (provider
abstraction + Anthropic client + the smoke-test gate, `langchain-anthropic`
dependency), ADR 009 was folded into `architecture.md` (§1/§3/§4), and
`/awos:tasks 010` produced five vertical slices.

**Implementation (06-11 → 06-12)** ran the slices in order, each leaving the app
runnable: the `LLMProvider` abstraction with `BedrockProvider` as a **pure
zero-behavior refactor** (full suite green with zero test edits — the proof);
the `GRAPHIA_LLM_PROVIDER` config surface with typo and remote-contradiction
guards; `OllamaProvider` via `ChatAnthropic` against the local `/v1/messages`
(constructing with *zero* AWS or Anthropic credentials); a fail-fast boot
preflight with plain-language errors ("Couldn't reach Ollama… `ollama serve`",
missing models named with their `ollama pull` lines); and `make ollama-smoke` —
a per-schema structured-output instrumentation harness that counts raw
tool-call outcomes *underneath* the game's retry-then-fallback masking. 45 new
offline tests; the mocked suite never touches a provider.

**The ADR-010 gate fired twice, for two different reasons.** First run: the
"offline" game died at 2.5s on `UnauthorizedSSOTokenError` — not the model but
an **environment leak**: `make_career_emitter` gates on the Memory id alone, so
a wire-env'd `.env` had even local games emitting career events to AgentCore
Memory (functional-spec §2.2 violation, caught only because SSO happened to be
expired). Second, with the harness env-isolated: the candidate default
`qwen2.5:7b` was **rejected** — 40/40 `DayAction` tool-call failures, the model
answering in prose and every Day turn falling back to canned lines — while
**`qwen3-coder:30b` verified clean** (0 failures across Roster/Pointing/
DayAction; the user's live game later exercised `Ballot` too, via a real vote).
The verified pair became the config defaults and the README gained a "Play
offline with Ollama" quickstart. ADR 010's Anthropic-compat bet **held** — the
fallback clause was never invoked.

**Three follow-ups closed the act:** the **offline gate** (provider=`ollama`
blanks all cloud store ids at config load — §2.2 now holds by construction, no
`.env` hand-editing); the **`PlayerState` checkpoint-serializer allowlist**
(a shared `make_checkpoint_serde()` at both saver sites, killing 45,609
deprecation warnings per suite run before a future langgraph upgrade turned
them into hard failures); and the **`fake_sonnet`/`fake_haiku` → `fake_large`/
`fake_small` fixture rename** (249 refs across 20 files — the last survivors of
the Nova misnomer). Spec 010 was then **verified Completed** (14/14 criteria)
and **Phase 4 closed**, with both roadmap sub-items ticked — including the
long-unticked *AWS Profile / SSO Credentials*, shipped incrementally since May.

---

## The Slice 7 saga (2026-05-13 → 05-15)

The single most eventful stretch. ADR 002 had chosen *runtime-embedded* Gateway
tool handlers — one container hosting both the agent and the MCP tool server.

1. **First attempt (05-13)** built a FastMCP server inside the Runtime and the
   Gateway resources around it (`mcp_server`-type targets).
2. **ADR 004 (05-13)** logged a provider-gap workaround: `hashicorp/aws 6.44.0`
   couldn't express the IAM credential provider that `mcp_server` targets need.
3. **The wall:** an AgentCore Runtime's `protocol_configuration` is mutually
   exclusive — `HTTP` (agent stream) vs `MCP` (tool server). One container
   cannot host both.
4. **ADR 005 (05-14)** superseded ADR 002 outright: pivot to **Lambda-target
   Gateway tools** — the canonical AgentCore pattern, no protocol clash. Two
   zip-deployed Lambdas replaced the in-Runtime FastMCP server.
5. **Five remote-only bugs (05-15)**, each invisible to the all-mocked test
   suite, surfaced and were fixed one by one: an `asyncio.run()`-in-running-loop
   crash, an `httpx.Auth` `isinstance` check new in `mcp 1.27+`, Gateway's
   `<target>___<tool>` tool-name namespacing, macOS wheels shipped in the Linux
   Lambda zips, and three UI methods reading an empty local graph state.
6. With Lambda targets working, **ADR 004's workaround became moot** and was
   marked superseded too.

---

## Spec 002 slice ledger

| Date  | Increment                | What shipped                                          | Tests | Tasks   |
| ----- | ------------------------ | ----------------------------------------------------- | ----- | ------- |
| 05-12 | Slices 1-3               | Auth refactor · Terraform skeleton · Runtime image    | —     | 15/59   |
| 05-12 | Slice 3 follow-on        | Makefile task-runner · ECR force-delete safeguard     | —     | 23/67   |
| 05-13 | Slice 4                  | Full game vs hosted Runtime · HITL over the wire      | 42    | 38/67   |
| 05-13 | Nova switch (ADR 003)    | Anthropic Claude → Amazon Nova Pro/Lite               | 41    | —       |
| 05-13 | Slices 5-6               | `[local]`/`[remote]` badge · AgentCore Memory diary   | 62    | 40/66   |
| 05-13 | Slice 6 follow-on        | DiaryStore factory fix · `inspect-diary` utility      | 62    | —       |
| 05-13 | Slice 7 (1st attempt)    | FastMCP server + Gateway resources (ADR 002 shape)    | 83    | 47/66   |
| 05-14 | Slice 7 pivot (ADR 005)  | Lambda-target Gateway tools; FastMCP server removed   | 80    | —       |
| 05-15 | Slice 7 bug fixes        | 5 remote-only defects fixed                           | 83    | 61/76   |
| 05-15 | Slice 8                  | Observability + 30-day retention + failure modal      | 92    | 70/76   |
| 05-16 | CR 003 follow-ons        | OTEL recipe fix · IAM permissions · Tx Search policy  | 93    | —       |
| 05-18 | Slice 9                  | local↔remote equivalence test suite                   | 100   | 76/82   |
| 05-18 | Slice 10                 | `terraform destroy` verification · README walkthrough | 102   | 80/82   |
| 05-18 | CR 004 / Slice 11 plan   | Revised §2.1/§2.2 launch criteria; §2.4.2 read-back   | 102   | 80/85   |
| 05-20 | Slice 11                 | Diary round-trip · Memory fallback · deploy hint      | 107   | 85/85   |
| 05-20 | Spec 002 Verified         | `/awos:verify` flipped Status → Completed             | 107   | 85/85   |
| 05-20 | Tutorial 002              | Full 7-section walkthrough + companion concept ledger | 107   | 85/85   |

_The task denominator drifts (59 → 67 → 66 → 76 → 82 → 85) because each slice's
planning and its follow-on subsections add or revise sub-tasks; the final 85
covers Slices 1-11. The test count dips twice — 42 → 41 at the Nova switch (one
prompt-pinned test referenced a Claude-specific schema and was retired) and
83 → 80 at the ADR-005 pivot (four obsolete FastMCP server-side tests deleted) —
then climbs to 107 as Slices 8-11 add observability, equivalence, and round-trip
coverage._

---

## Two recurring themes from the history

- **Real deploys find what mocked tests can't.** Every Phase 2 slice with cloud
  surface area spawned a "follow-on" of bug fixes that only a real `terraform
  apply` + `--remote` game surfaced: the Slice 3 deploy/destroy cycle, the Slice 6
  vanishing-diary factory bug, the Slice 7 five-bug chain, the Slice 8 flat
  trace tree (fixed by IAM permissions, not by the OTEL recipe — the natural
  first guess), and the §2.4.2 round-trip gap that `/awos:verify` itself caught
  *after* all the slice-level checks were green. The all-mocked pytest suite
  stays green throughout — it guards regressions, not integration reality.
- **The workflow tooling was built alongside the project.** The AWOS commands
  themselves were authored mid-stream: `/awos:change-request` (05-05),
  `/awos:adr` (05-10), `/awos:tutorial` (05-12). The process and the product
  co-evolved.

---

## Change Requests

| CR  | Date       | Title                                                              | Status   |
| --- | ---------- | ------------------------------------------------------------------ | -------- |
| 001 | 2026-05-05 | AgentCore deployment + AI tool-use promoted to v1.1 scope          | Accepted |
| 002 | 2026-05-06 | Long-term AgentCore Memory in; AI tool-use demoted to Phase 7      | Accepted |
| 003 | 2026-05-15 | AgentCore Observability delivers navigable per-session trace trees | Accepted |
| 004 | 2026-05-18 | Revise §2.2 launch error handling and the §2.1 next-step hint      | Accepted |
| 005 | 2026-06-16 | Reframe spec 013 acceptance: commit to effort, not results (AI)    | Accepted |

## Architecture Decision Records

| ADR | Date       | Title                                                   | Status                |
| --- | ---------- | ------------------------------------------------------- | --------------------- |
| 001 | 2026-05-07 | Hosted AgentCore Runtime with preserved local mode      | Accepted              |
| 002 | 2026-05-12 | Runtime-embedded Gateway tool handlers                  | Superseded by ADR 005 |
| 003 | 2026-05-13 | Bedrock model family — Amazon Nova over Claude          | Accepted              |
| 004 | 2026-05-13 | Gateway target IAM-auth via CLI workaround              | Superseded by ADR 005 |
| 005 | 2026-05-14 | Gateway tools via Lambda targets                        | Accepted              |
| 006 | 2026-05-23 | Test role-pinning via `GRAPHIA_ROLE` (amended Slice 5)  | Accepted              |
| 007 | 2026-05-28 | Two-tier long-term memory stats (exact now, semantic later) | Superseded by ADR 008 |
| 008 | 2026-05-30 | Long-term memory via the self-managed pipeline          | Accepted              |
| 009 | 2026-06-11 | Pluggable LLM provider abstraction (Bedrock + Ollama)   | Accepted              |
| 010 | 2026-06-11 | Anthropic-compatible protocol for the Ollama provider   | Accepted              |

## Specs & tutorials

| Spec | Title                                       | Slices | Status    |
| ---- | ------------------------------------------- | ------ | --------- |
| 001  | Playable Skeleton                           | 9      | Completed |
| 002  | Hosted AgentCore Deployment                 | 11     | Completed |
| 003  | Reliable Game Exit Controls                 | 3      | Completed |
| 004  | Robust /vote Input Validation               | 4      | Completed |
| 005  | Play-As-Role via Environment Variable       | 5      | Completed |
| 006  | Long-Term Cross-Game Memory & Career Stats  | 8      | Completed |
| 007  | Fair Day Speaking Order                     | 2      | Completed |
| 008  | Same-Round Message Visibility               | 2      | Completed |
| 009  | AI Collusion Awareness                      | 1      | Completed |
| 010  | Local Ollama Provider                       | 5      | Completed |
| 011  | AI Blunder Tracking (quality ledger)        | 6      | Completed |
| 012  | Eval Ledger Viewer                          | 7      | Completed |
| 013  | AI Behavioral Integrity & Outcome Tracking  | 4      | Completed |

_Spec 006 was verified Completed on 2026-06-03 (all 32 acceptance criteria, Phase 3 roadmap bullets ticked) and Tutorial 006 published. Specs 007–009 (the Day-phase integrity trio) are now all **verified Completed**; 009's collusion nudge was revised to an **anti-parrot** reword after a real-Nova experiment showed the original wording drove a Day-dialogue repetition spiral._

Per-increment learning tutorials live under `context/tutorials/`: `001`, the
final `002` (depth-first walkthrough of all eleven Spec 002 slices), `004`
(the LangGraph interrupt/resume-pump gotcha), and `005` (the determinism
posture as the conceptual spine — `GRAPHIA_ROLE` and the seed retirement).
Tutorial `003` was intentionally skipped — that index is left open.
Tutorial `006` is now **published** — a depth-first walkthrough of the
self-managed-strategy/S3/SNS/Lambda pipeline (ADR 007 → 008 rewrite, the
four real-deploy bugs and what catches each), opening with a candid note
that the two-tier Memory pipeline is deliberately over-engineered for a
stats counter — a technology demonstration, not a need-driven design. An interim
`002-hosted-agentcore-deployment-v2` draft (Slices 1-4 + the Nova switch,
pre-Lambda-pivot) sits alongside as a historical artifact and will be removed
when no longer interesting.

Tutorials `007`–`009` are now **published**, completing the Day-phase trio's
documentation: `007` (fair speaking order), `008` (same-round visibility), and
`009` — a methodology walkthrough of the paired real-Nova **experiment** that
chose the anti-parrot fix (how to evaluate and fix non-deterministic LLM
behaviour, not just the one-line prompt change).

### Act 11 — AI Blunder Tracking: turning AI quality into a tracked, trustworthy number (2026-06-13)

A play-test observation kicked it off: beyond repetition, the AI commits a family of
self-consistency blunders — voting to execute *itself*, Mafiosi voting to execute
*teammates*, talking about itself in the third person — all invisible to the
maintainer except by luck, and unmeasured. **Spec 011 (AI Blunder Tracking)** set out
to make AI quality a *tracked* property: a make-gated harness (`make blunder-eval`)
that plays real games on either provider, counts the blunder family, and appends one
record per run to a repo-committed YAML ledger (`evals/blunder-ledger.yaml`) — "baby
MLOps". The chain ran spec → tech (which **dropped self-accusation** as too
keyword-fragile and **split each vote blunder** into separate initiation/Yes-ballot
measures) → tasks (six vertical slices) → implement.

Two slices carried the interesting craft. The **capture proxy** (extending spec 010's
reliability-counting `InstrumentedModel` from counting to *capturing*) measures
`self_vote.initiation` — a self-targeted AI vote the game's turn-handler rejects
before it reaches state, so the only place to see it is the raw payload — and
attributes it to the speaker by parsing the invoke *prompt* rather than a re-entrant
`graph.get_state()` (dodging the stale-snapshot trap that bit `test_slice7_vote`
earlier). The detectors parse the game's *own* message templates (imported as anchors,
so a reword breaks a test, not a metric), and a no-opportunity metric is *omitted*,
not reported as a misleading 0.

The user pushed twice on rigor — "three games is too few" and "did you keep the
statistical machinery?" — and both pushes paid off. A **Wilson confidence interval**
now ships with every rate (closed-form, readable at a glance), and a proper **20-game
baseline reversed the 3-game read**: at n=3 repetition looked identical across
providers (≈0.45, "structural"); at n=20 the intervals separated cleanly (Nova 0.554
[0.525, 0.583] vs qwen 0.389 [0.357, 0.422]) — provider-dependent after all, with
qwen's vote-incoherence (self-execution 63%, teammate-execution 86%) now exposed. The
same "rigor reverses the noisy pilot" lesson as spec 009, re-lived literally. A bonus
gameplay fix rode along — the user noticed AIs confabulating about a publicly-revealed
role, which traced to the Moderator's announcements being labelled `SystemMessage:`
(not `Moderator:`) in the AI's prompt *and* a private-whisper leak into the
day-context — both fixed (commit `ef452d3`). Spec 011 was verified **Completed (23/23)**
and tutorialised.

### Act 12 — Eval Ledger Viewer: making the quality history legible (2026-06-13 → 06-14)

Spec 011 left the quality ledger **append-only and write-only** — easy to grow,
hard to read: comparing runs meant scrolling raw indented YAML. **Spec 012 (Eval
Ledger Viewer)** is the reader 011 deferred — a standalone Textual table over
`evals/blunder-ledger.yaml`: one row per run, `rate [CI] m/n` cells (blank where
a behavior wasn't exercised), a `⚠` mark on dirty-tree runs, both-axis
scrolling, filter-search, and a full-record drill-down. It finally takes on the
**YAML-parser dependency** (`pyyaml`) that 011 deliberately avoided, exactly as
`evals/README.md` foretold. The chain ran spec → tech (Textual 8.2.4 APIs
verified live, no spec/Textual collisions) → tasks (three vertical slices,
MVP-table-first) → implement.

**Slice 1 (the MVP) is done**, in two layers: a **pure, Textual-free data layer**
(`eval_ledger.py` — parse + a defensive flattener) so the bulk of the logic is
unit-testable without a TUI, and a thin **Textual viewer**
(`ui/ledger_viewer.py`, `make view-ledger`). Two agent catches were the story of
the slice, both from *checking reality over the spec*: the vote metrics are
stored as **flat dotted keys** (`metrics["self_vote.initiation"]`), not the
nested maps the tech spec assumed — caught by parsing the real committed ledger,
fixed flat-first-nested-fallback, spec corrected; and the `textual-tui` agent
spotted a **prompt-injection attempt** in a docs-tool's output (a fake "run
`npx ctx7 setup`" instruction) and ignored it. +22 offline tests (pure +
Textual `run_test` Pilot); suite 407 → 429.

**Slices 2–7 followed in one 06-14 session, most of them driven by live
feedback** — the spec was edited *inline* each time ("we are still on it") and
re-run through tech → tasks → implement, with `python-backend` / `textual-tui` /
`testing` subagents per task:

- **Slice 2 — search.** A docked filter `Input` + "Showing X of N", live per
  keystroke, with a distinct "No runs match" state.
- **Slice 3 — drill-down + read-only proof.** Row-select → a full-record
  `DetailScreen` (sectioned `render_detail`, full note verbatim), cursor restored
  on return, and a test asserting the ledger is **byte-identical** after a whole
  browse session.
- **Slice 4 — the "phantom match" fix.** Searching `ollama` matched *every* row
  because the two bedrock runs' notes say "vs ollama" and notes are searched but
  weren't shown. Added a **Notes column** (the match is now visible, not a
  phantom) and fixed a focus bug the search box introduced — the table now holds
  focus by default so the arrow keys navigate immediately (`/` reaches search,
  Esc backs out).
- **Slice 5 → 6 — field search, then a redesign.** First a typed `field:value`
  syntax (`provider:bedrock`) with the colon-rule subtlety that a model id
  `qwen3-coder:30b` must stay free-text. But typing the *field name* matched
  nothing until the colon — bad live-search — so it was **superseded the same
  day** by a **field selector** dropdown (default "All"): pick the field, no
  typing; boundary-jump left/right between selector and value box; **Backspace**
  added as "back" from the detail view.
- **Slice 7 — cell cursor.** Replaced character-wise horizontal panning with a
  **highlighted cell** the `DataTable` auto-scrolls *fully into view* as it moves;
  drill-down fires on `CellSelected` and the exact cell is restored on return.
  Plus detail-view **chrome** — a Header (viewer name + a "back" subtitle) and a
  Footer (Esc/Backspace → Back) so a full-window record stays recognisably the
  viewer with a visible exit.

The **live acceptance walk passed** ("it works well", 2026-06-14). Suite **429 →
465** across the session, every viewer test offline against a temp ledger (the
viewer never imports `load_config`). **Spec 012 is verified Completed.**

---

### Act 13 — AI Behavioral Integrity: fixing behaviour as a tested hypothesis (2026-06-15 → 06-16)

Spec 011's n=20 baseline had exposed three pathologies; spec 012's viewer made them
legible; **Spec 013** set out to *fix* them — and to fix them *honestly*. The
diagnosis from the data was one root cause: the gameplay prompts never tell an AI its
own secret role, side, or teammates, so it votes to execute itself (0.63), votes to
execute its own Mafia teammate (0.86), or (on the cloud model) never calls a Day vote
at all. The increment ran **measure-first within one spec**: Slice 1 added two ledger
blocks (`outcomes` = win-rate by side; `vote_activity` = initiations by side and
game-day, with an **explicit-zero** so a silent Day reads as a committed `0/0` — the
deliberate inverse of 011's absent-omission); Slice 2 committed a **pre-fix baseline**
on the unchanged behaviour (clean tree, both providers); only then did Slice 3 inject
the fix — role + win-condition + teammate list + a ballot relationship-flag, directly
into the prompts — under a hard **knowledge-boundary invariant** (a Citizen is told
nothing about any other player's allegiance; only Mafia get a teammate list). A real
coupling bug surfaced and was caught: the reworded prompt's literal anchor nearly broke
011's prompt-parse speaker resolver.

The **after-picture (Slice 4)** was honestly mixed. **Confirmed:** role/team grounding
drove qwen's self-execution votes 0.57→0.0 and teammate-execution 0.67→0.0 (third-person
self-talk 0.088→0.016). **Refuted:** the Day-passivity nudge did not wake Nova — still
zero votes. **Open:** the town still won 0/20 on both providers (coherent individual
votes ≠ town coordination). Under the spec's original outcome-based acceptance this read
as a failure despite the measurement working perfectly — which surfaced a principle,
captured in **[CR 005](context/change-requests/005-ai-behaviour-acceptance-effort-not-results.md)**:
for non-deterministic AI work the project commits to a measured **effort** (a tested
hypothesis), **not** to a guaranteed **result**. Reframed that way, every behaviour
criterion was *tested against the baseline* and so satisfied — and **Spec 013 verified
Completed** (12/12), with the unachieved improvements (Nova passivity, town coordination)
moved to follow-up specs rather than buried as failure. Suite **465 → 526**; tutorial 013
published (the `012` tutorial slot is left open for the Eval Ledger Viewer).

---

## What's next

**Phase 3 — Long-Term Cross-Game Memory & Career Stats** is now closed:
Spec 006 is **verified Completed**, **Tutorial 006** is published, and the
live deploy is green end-to-end (`make verify-pipeline`, six checks against
the `eafa1ee` runtime + Lambda).

The **Day-phase integrity trio is done** — 007, 008, 009 all verified
Completed **and tutorialised**. **Phase 4 — AI Provider Flexibility** is now
**closed**: Spec 010 (Local Ollama Provider) implemented, smoke-gated, live
play-tested, and verified Completed; ADRs 009/010 Accepted and folded into the
architecture; both roadmap sub-items ticked. The next roadmap item is **Phase 5
— Setup Flexibility** (Configurable Role Counts) and **Richer Night Resolution**
(Multi-Round Mafia Consensus) — start with `/awos:spec`.

**Spec 011 (AI Blunder Tracking) is verified Completed and tutorialised**
(2026-06-13): a make-gated harness + a repo-committed, append-only quality ledger
with run provenance and Wilson confidence intervals, and a reliable n=20 baseline
that reversed the n=3 read. **Spec 012 (Eval Ledger Viewer) is verified Completed**
(2026-06-14): `make view-ledger` opens a scrollable, searchable table over that
ledger with a field-selector scope, a cell cursor that scrolls the highlight
fully into view, and a full-record drill-down — the live walk passed. **Spec 013
(AI Behavioral Integrity) is verified Completed and tutorialised** (2026-06-16,
under CR 005): role/team grounding fixed the local model's vote-incoherence
(self/teammate-execution → 0.0); the Day-passivity nudge was refuted and the
town-win problem stayed open, both moved to follow-up specs under the
**effort-not-results** acceptance principle. The roadmap's next *feature*
is still **Phase 5 — Configurable Role Counts / Multi-Round Mafia Consensus**.
Open follow-ups: the **Nova Day-passivity** mechanical attempt and the deeper
**town-coordination / Day-decisiveness** problem (both spec-able now); the
**Eval Ledger Viewer tutorial** (the held-open `012` slot); the parked AWOS
`_`-command renames + `handoff.md`; and `product-definition.md` still calls the
Ollama provider "future".

Immediate follow-ups from the 06-09→10 session:

1. ~~Ship 009 remotely~~ — done (deployed 2026-06-10 after the Podman
   clock-skew detour).
2. ~~Fixture rename~~ — done in the Phase 4 close-out (06-12).
3. **Dialogue-diversity gate** — the `make repetition-experiment` harness could
   graduate from report-only to a `--min-distinct` regression gate once a
   threshold is settled.

Older loose ends still open: the product `architecture.md` describes remote
stats in pre-ADR-008 terms; Tutorial `003` remains the deliberately-open slot;
the buddah plugin's fork-side hook fix (`${CLAUDE_PLUGIN_ROOT}` + `/buddah:*`
namespace); and the AWOS `_`-prefixed command renames remain uncommitted.

The repo is public at **github.com/tigra/graphia**, so future increments ship
in the open.
