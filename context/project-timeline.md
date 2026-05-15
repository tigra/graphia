# Graphia — Project Timeline

A high-level history of the project, reconstructed from `git log` and the
`context/` artifacts: how scope changed (Change Requests), how the architecture
was decided (Architecture Decision Records), and how the work was executed (specs
broken into vertical slices). Covers **2026-04-29 → 2026-05-15**.

Graphia is built with the **AWOS spec-driven workflow** — every increment flows
`product → roadmap → architecture → spec → tech → tasks → implement → verify → tutorial`,
with CRs logging scope shifts and ADRs logging architectural decisions along the way.

---

## Timeline

```mermaid
%%{init: {'themeVariables': {'doneTaskBkgColor': '#6aa84f', 'doneTaskBorderColor': '#38761d', 'activeTaskBkgColor': '#e69138', 'activeTaskBorderColor': '#b45f06'}}}%%
gantt
    title Graphia — CRs, ADRs, and Spec Execution
    dateFormat YYYY-MM-DD
    axisFormat %b %d

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
    Slices 1-3 — Auth · Terraform · Runtime image        :done, s13, 2026-05-12, 1d
    Slice 3 follow-on — Makefile task-runner             :done, s3f, 2026-05-12, 1d
    Slice 4 — Full remote game · HITL over the wire      :done, s4, 2026-05-13, 1d
    Slices 5-6 — Corner badge · AgentCore Memory diary   :done, s56, 2026-05-13, 1d
    Slice 6 follow-on — DiaryStore factory fix           :done, s6f, 2026-05-13, 1d
    Slice 7 — Gateway tools · ADR 002 to 005 pivot       :done, s7, 2026-05-13, 3d
    Slice 7 follow-on — 5 remote-only bug fixes          :done, fix, 2026-05-15, 1d
    Slices 8-10 — Observability · tests · destroy        :active, s810, 2026-05-15, 4d
```

**How to read it.** Each visual channel encodes exactly one thing:

- **Colour = status.** Green = completed / accepted / in effect · Red = superseded · Orange = pending.
- **Shape = kind.** Diamonds are point events (CRs, ADRs, spec milestones); bars are executed slice work spanning real days.
- **Sections = project phase.** ADRs are listed first within Phase 2, then the slice bars, so a superseded ADR (red diamond) is never mistaken for blocked work.

So the only red marks are the two superseded ADRs (002, 004); CRs are green because their scope changes stand; the one orange bar is the work still ahead.

---

## Work breakdown structure

The timeline above shows *when*; this shows *what* — each spec decomposed into
work areas and the vertical slices under them. Slice level is the leaf here; every
slice decomposes further into the sub-task checklist in its spec's `tasks.md`.

A WBS can be drawn two ways here. **Spec 001** uses a flowchart tree — it shipped
in a single commit, so there is no per-slice schedule to plot. **Spec 002** uses a
*gantt* instead: gantt sections carry the WBS work areas and the slice tasks carry
real dates, so the same breakdown doubles as a schedule. (A gantt nests only two
levels — section → task — so the spec name sits in the title rather than as a
tree root.)

### Spec 001 — Playable Skeleton

```mermaid
flowchart TD
    S1["Spec 001 — Playable Skeleton<br/>Status: Completed · 9 slices"]:::spec
    S1 --> G11["1.1 Bootstrap & Roster"]:::area
    S1 --> G12["1.2 Core Game Loop"]:::area
    S1 --> G13["1.3 Endgame & Polish"]:::area
    G11 --> T1["Slice 1 — App boot, name entry, logging"]:::done
    G11 --> T2["Slice 2 — Roster + public Moderator intro"]:::done
    G11 --> T3["Slice 3 — Live AI names via Haiku"]:::done
    G11 --> T4["Slice 4 — Role assignment + private reveal"]:::done
    G12 --> T5["Slice 5 — Night 1 end-to-end"]:::done
    G12 --> T6["Slice 6 — Day 1 speaking + victim reveal"]:::done
    G12 --> T7["Slice 7 — Vote-to-execute mechanics"]:::done
    G13 --> T8["Slice 8 — Win detection + end-of-game screen"]:::done
    G13 --> T9["Slice 9 — Polish: spectator, Ctrl-C, draw cap"]:::done
    classDef spec fill:#3d85c6,stroke:#0b5394,color:#fff
    classDef area fill:#d9d9d9,stroke:#999999,color:#000
    classDef done fill:#6aa84f,stroke:#38761d,color:#fff
    classDef pending fill:#e69138,stroke:#b45f06,color:#fff
```

### Spec 002 — Hosted AgentCore Deployment (WBS as a gantt)

Sections are the WBS work areas; tasks are the slices, carrying real dates.

```mermaid
%%{init: {'themeVariables': {'doneTaskBkgColor': '#6aa84f', 'doneTaskBorderColor': '#38761d', 'activeTaskBkgColor': '#e69138', 'activeTaskBorderColor': '#b45f06'}}}%%
gantt
    title Spec 002 WBS — Hosted AgentCore Deployment
    dateFormat YYYY-MM-DD
    axisFormat %b %d

    section 2.1 Foundation
    Slice 1 — Config refactor + auth posture           :done, 2026-05-12, 1d
    Slice 2 — Resource discovery + Terraform skeleton  :done, 2026-05-12, 1d
    Slice 3 — Runtime container image + resource       :done, 2026-05-12, 1d
    Slice 3 follow-on — Makefile task-runner           :done, 2026-05-12, 1d

    section 2.2 Remote Execution
    Slice 4 — AgentCore client + full remote game      :done, 2026-05-13, 1d

    section 2.3 UI & Memory
    Slice 5 — local / remote corner badge              :done, 2026-05-13, 1d
    Slice 6 — AgentCore Memory diary store + resource  :done, 2026-05-13, 1d

    section 2.4 Gateway Tool Surface
    Slice 7 — Gateway tool surface (ADR 002 to 005)    :done, 2026-05-13, 3d
    Slice 7 follow-on — Lambda pivot + 5 remote fixes  :done, 2026-05-15, 1d

    section 2.5 Hardening & Teardown
    Slice 8 — Observability + retention + modal        :active, 2026-05-15, 2d
    Slice 9 — Equivalence tests + end-to-end smoke     :active, 2026-05-17, 2d
    Slice 10 — terraform destroy verification + README :active, 2026-05-19, 2d
```

_Colour scheme matches the timeline: green = done, orange = pending. Dates for
Slices 8-10 are projected, not actual. The Spec 001 flowchart uses blue for the
spec (WBS root) and grey for a work area (a grouping, not itself executable)._

---

## What was going on — three acts

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

### Act 3 — Phase 2: hosting Graphia on AgentCore (2026-05-12 → 05-15, ongoing)

**Spec 002 — Hosted AgentCore Deployment** is being executed slice by slice:

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

**Current state:** Slices 1-7 complete and verified — a full `--remote` game plays
end-to-end through `agent → Gateway → Lambda → Memory`, **83/83 tests pass**,
**61/76 task items done**. Slices 8-10 remain. Spec 002 is still `Draft` — not yet
`/awos:verify`-ed.

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

| Date  | Increment              | What shipped                                          | Tests | Tasks   |
| ----- | ---------------------- | ----------------------------------------------------- | ----- | ------- |
| 05-12 | Slices 1-3             | Auth refactor · Terraform skeleton · Runtime image    | —     | 15/59   |
| 05-12 | Slice 3 follow-on      | Makefile task-runner · ECR force-delete safeguard     | —     | 23/67   |
| 05-13 | Slice 4                | Full game vs hosted Runtime · HITL over the wire      | 42    | 38/67   |
| 05-13 | Nova switch (ADR 003)  | Anthropic Claude → Amazon Nova Pro/Lite               | 41    | —       |
| 05-13 | Slices 5-6             | `[local]`/`[remote]` badge · AgentCore Memory diary   | 62    | 40/66   |
| 05-13 | Slice 6 follow-on      | DiaryStore factory fix · `inspect-diary` utility      | 62    | —       |
| 05-13 | Slice 7 (1st attempt)  | FastMCP server + Gateway resources (ADR 002 shape)    | 83    | 47/66   |
| 05-14 | Slice 7 pivot (ADR 005)| Lambda-target Gateway tools; FastMCP server removed   | 80    | —       |
| 05-15 | Slice 7 bug fixes      | 5 remote-only defects fixed                           | 83    | 61/76   |

_The task denominator drifts (59 → 67 → 66 → 76) because each slice's planning and
its follow-on subsections add or revise sub-tasks. The test count dips 83 → 80 at
the ADR-005 pivot — the pivot deleted four obsolete FastMCP server-side tests._

---

## Two recurring themes from the history

- **Real deploys find what mocked tests can't.** Every Phase 2 slice with cloud
  surface area spawned a "follow-on" of bug fixes that only a real `terraform
  apply` + `--remote` game surfaced: the Slice 3 deploy/destroy cycle, the Slice 6
  vanishing-diary factory bug, the Slice 7 five-bug chain. The all-mocked pytest
  suite stays green throughout — it guards regressions, not integration reality.
- **The workflow tooling was built alongside the project.** The AWOS commands
  themselves were authored mid-stream: `/awos:change-request` (05-05),
  `/awos:adr` (05-10), `/awos:tutorial` (05-12). The process and the product
  co-evolved.

---

## Change Requests

| CR  | Date       | Title                                                          | Status   |
| --- | ---------- | -------------------------------------------------------------- | -------- |
| 001 | 2026-05-05 | AgentCore deployment + AI tool-use promoted to v1.1 scope      | Proposed |
| 002 | 2026-05-06 | Long-term AgentCore Memory in; AI tool-use demoted to Phase 7  | Proposed |

## Architecture Decision Records

| ADR | Date       | Title                                              | Status                |
| --- | ---------- | -------------------------------------------------- | --------------------- |
| 001 | 2026-05-07 | Hosted AgentCore Runtime with preserved local mode | Accepted              |
| 002 | 2026-05-12 | Runtime-embedded Gateway tool handlers             | Superseded by ADR 005 |
| 003 | 2026-05-13 | Bedrock model family — Amazon Nova over Claude     | Accepted              |
| 004 | 2026-05-13 | Gateway target IAM-auth via CLI workaround         | Superseded by ADR 005 |
| 005 | 2026-05-14 | Gateway tools via Lambda targets                   | Accepted              |

## Specs & tutorials

| Spec | Title                       | Slices | Status                              |
| ---- | --------------------------- | ------ | ----------------------------------- |
| 001  | Playable Skeleton           | 9      | Completed                           |
| 002  | Hosted AgentCore Deployment | 10     | Draft — slices 1-7 done, 8-10 to go |

Per-increment learning tutorials live under `context/tutorials/`: `001`, an
interim `002` (Slices 1-3), and `002-v2` (Slices 1-4 + the Nova switch) — the
latter now partly stale after the ADR-005 Lambda pivot.

---

## What's next

1. **Slices 8-10** of Spec 002 — AgentCore Observability + 30-day log retention +
   a failure modal; equivalence tests (local vs remote parity); `terraform
   destroy` cleanup verification.
2. **`/awos:verify`** Spec 002 once all 15 remaining task items are `[x]`,
   flipping it to Completed.
3. **Regenerate Tutorial 002** — the ADR-005 Lambda pivot left the current
   tutorial describing a runtime-embedded shape that no longer exists.
4. **Phase 3** — Long-Term Cross-Game Memory (career stats), per the roadmap.
