# Graphia

A single-player, console **Mafia** game that doubles as a hands-on reference implementation of advanced **LangGraph** orchestration and **AWS Bedrock AgentCore** deployment patterns — multi-agent state graphs, private per-agent state, human-in-the-loop interrupts, async streaming to a terminal UI, and an optional hosted runtime on AgentCore (Runtime + Gateway + Memory + Observability).

You play one human among six AI townsfolk. Some are Mafia. Survive the nights, argue through the days, and vote to execute the people you suspect — or get executed first.

> **Why it exists:** most multi-agent examples are toy tasks with no real deployment story. Graphia is a genuinely playable game whose source makes each advanced LangGraph and AgentCore concept easy to locate, run, and lift into a real project — both locally and against a hosted cloud runtime.

![Graphia running in the terminal — Night phase, the human (a Mafioso) choosing a target from the pointing modal](docs/screenshot.png)

---

## Highlights

- **One Textual TUI driving one LangGraph `StateGraph`.** Night → Day phase alternation, first-night private Mafia introductions, asynchronous day chat, vote-to-execute, win detection, end-of-game recap.
- **Human-in-the-loop via `interrupt()` / `Command(resume=…)`.** The human's turns pause the graph and resume on input — the same way in local and hosted modes.
- **Two run modes, one codebase.** A no-AWS **local** mode for fast iteration, and a **remote** mode where the same compiled graph runs inside a Bedrock AgentCore Runtime. Selected with a single `--remote` flag.
- **AgentCore surface, demonstrated end-to-end.** Per-game diaries through an AgentCore Gateway-fronted tool surface, AgentCore Memory persistence, and CloudWatch observability with navigable per-session trace trees — all provisioned via Terraform.
- **Spec-driven, fully documented.** Built with the [AWOS](https://github.com/provectus/awos) workflow; every increment leaves behind a spec, a tech plan, a task list, and a learning tutorial under [`context/`](context/).

---

## Working with `make`

The repo-root [`Makefile`](Makefile) is the project's task runner — **prefer `make <target>` over invoking `uv`, `terraform`, `podman`/`docker`, or `aws` directly.** The targets wrap the multi-step workflows (image build → ECR push → Terraform apply → `.env` wiring, and the reverse for teardown), auto-load `.env`, derive the AWS account from your active profile, and run Terraform inside a pinned container via the `./tf` wrapper, so you get the same behaviour regardless of what's installed on your host.

Run `make help` to list everything. The targets you'll use most:

| Target | What it does |
|---|---|
| `make play` | Play a game in **local** mode (no AgentCore). |
| `make play-remote` | Play against the **deployed** AgentCore Runtime. |
| `make deploy` | First-time provision: build image, bootstrap ECR, push, `terraform apply`, wire `.env`. |
| `make redeploy` | Rebuild + push + apply for an existing stack (after code changes). |
| `make destroy` | Tear down the whole AgentCore stack (safe two-step ECR purge). |
| `make build` | Build just the runtime container image. |
| `make inspect-diary` | Pretty-print per-game diary entries from the deployed Memory. |

Direct commands (`uv run python -m graphia`, `terraform …`, `aws …`) still work and are documented below for clarity, but reach for `make` first.

---

## Quick start (local mode)

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.10. LLM calls go through Amazon Bedrock, so you need AWS credentials for a Bedrock-enabled account.

```bash
# 1. Point at a Bedrock-enabled AWS profile (set GRAPHIA_* and AWS_PROFILE in .env, or export):
export AWS_PROFILE=<your-aws-profile>
aws sso login --profile <your-aws-profile>     # if using SSO

# 2. Play (local mode — no AgentCore, runs entirely on your machine).
make play
```

`make play` runs `uv run python -m graphia` under the hood. Graphia is a Textual app — run it in a real terminal, not an IDE "run" console. For a reproducible game (fixed role assignment and tie-breaks), set a seed in `.env` or inline:

```bash
GRAPHIA_SEED=1234 make play
```

**Controls:** type to speak on your turn; `/vote <name>` to call a vote to execute someone; `Esc` to quit (with a confirmation), `Ctrl+C` to force-quit.

---

## Remote mode (hosted on AgentCore)

The same game can run against a deployed AgentCore Runtime, provisioned by the Terraform module in [`infra/terraform/`](infra/terraform/README.md) and driven entirely through `make`:

```bash
make deploy        # build image, provision Runtime + Gateway + Memory + Observability, wire .env
make play-remote   # play against the deployed Runtime
make destroy       # tear it all down
```

The AWS account ID is derived from your active profile; no account or profile names are baked into source. See [`infra/terraform/README.md`](infra/terraform/README.md) for prerequisites and the full deploy/destroy walkthrough.

**Terraform runs inside a container — you don't install it.** Every `terraform` invocation goes through the [`infra/terraform/tf`](infra/terraform/tf) wrapper, which auto-detects **Podman or Docker** (either works) and runs a **pinned `hashicorp/terraform:1.13.1`** image, mounting the repo and your `~/.aws` config and forwarding `AWS_PROFILE`. The only host prerequisite for the IaC layer is a container runtime — no local Terraform install, and no "works on my Terraform version" drift: everyone and CI run the exact same Terraform + provider versions. The `make` targets call `./tf` for you, so you rarely invoke it directly.

---

## Testing

```bash
uv run pytest -q
```

The suite is fully mocked at the `ChatBedrockConverse` boundary (an autouse fixture fails loudly if any test reaches real Bedrock), so it runs offline and deterministically.

---

## How it works

```mermaid
flowchart LR
    subgraph client["Local machine"]
        UI["GraphiaApp<br/>(Textual TUI)"]
        DRV["drive_graph()<br/>resume pump + super-step stream"]
        UI <-->|"interrupt() / Command(resume=…)"| DRV
    end

    subgraph localmode["Local mode"]
        G1["StateGraph<br/>setup · night · day · endgame"]
        DS1["in-process<br/>diary store"]
        G1 --- DS1
    end

    subgraph remotemode["Remote mode (AWS)"]
        RT["AgentCore Runtime<br/>(same StateGraph)"]
        GW["AgentCore Gateway<br/>→ Lambda diary tools"]
        MEM["AgentCore Memory"]
        RT --> GW --> MEM
    end

    DRV -->|"local"| G1
    DRV -->|"--remote"| RT
```

- **Graph topology** lives in `src/graphia/graph.py`; nodes are grouped by phase under `src/graphia/nodes/`.
- **State** is one `GameState` `TypedDict` with reducers (`src/graphia/state.py`); "private" messages are a convention carried in message metadata and filtered by the UI.
- **The driver** (`src/graphia/driver.py`) iterates the synchronous graph stream inside a worker thread so it never blocks Textual's event loop, and pumps one resume value per super-step.

**For the detailed, current diagrams** (kept up to date in the per-increment tutorials):

- **Full compiled-graph topology** — every phase, node, and conditional edge of the game loop: [Tutorial 001 → Diagram](context/tutorials/001-playable-skeleton/tutorial.md#diagram).
- **Hosted AgentCore deployment topology** — Client → Runtime → Gateway → Lambda → Memory, end to end: [Tutorial 002 → Diagram](context/tutorials/002-hosted-agentcore-deployment/tutorial.md#diagram).
- **The `/vote` re-prompt control flow** — single-interrupt-per-node loop: [Tutorial 004 → Diagram](context/tutorials/004-robust-vote-input-validation/tutorial.md#diagram).

---

## Repository layout

| Path | What's there |
|---|---|
| [`src/graphia/`](src/graphia/) | The game: graph, nodes, state, driver, Textual UI, config. Entry point: `python -m graphia`. |
| [`infra/terraform/`](infra/terraform/) | Terraform module + `./tf` container wrapper provisioning the AgentCore stack. |
| [`tests/`](tests/) | Pytest suite, all-mocked at the Bedrock boundary. |
| [`context/`](context/) | The AWOS artifacts — see below. |
| [`Makefile`](Makefile) | Task runner (see [Working with `make`](#working-with-make)). |

---

## Built with AWOS (and our extensions)

Graphia is developed with **[AWOS](https://github.com/provectus/awos)** — Provectus's AI workflow for spec-driven development. Each feature flows through a chain of slash commands, every stage reading the previous stage's artifact and writing the next:

`product → roadmap → architecture → spec → tech → tasks → implement → verify → tutorial`

All artifacts live under [`context/`](context/) and are navigable here on GitHub:

- **Product & direction:** [`product-definition.md`](context/product/product-definition.md) · [`roadmap.md`](context/product/roadmap.md) · [`architecture.md`](context/product/architecture.md)
- **Specs** (functional spec + tech considerations + task list per increment): [`context/spec/`](context/spec/) — e.g. [`001-playable-skeleton`](context/spec/001-playable-skeleton/), [`002-hosted-agentcore-deployment`](context/spec/002-hosted-agentcore-deployment/), [`004-robust-vote-input-validation`](context/spec/004-robust-vote-input-validation/)

### Our extensions to AWOS

Three skills were added on top of the base chain and are a core part of how this project stays legible over time. They live in [`.awos/commands/`](.awos/commands/) and write into `context/`:

- **Change Requests** — [`/awos:change-request`](.awos/commands/change-request.md) → [`context/change-requests/`](context/change-requests/). Logged whenever a *previously-agreed requirement* shifts (scope, roadmap order, success criteria). Captured at the business-value level, kept separate from implementation detail. See e.g. [CR 001 — AgentCore + tool-use into scope](context/change-requests/001-agentcore-and-tools-in-scope.md) and [CR 003 — observability trace trees](context/change-requests/003-observability-navigable-trace-trees.md).
- **Architecture Decision Records** — [`/awos:adr`](.awos/commands/adr.md) → [`context/adr/`](context/adr/). Logged whenever an *architectural* choice is made or revised (deployment target, region, data store, vendor trade-off). Each records Context · Alternatives · Decision · Rationale · Consequences, and survives rewrites of the architecture doc. See e.g. [ADR 001 — hosted Runtime + local mode](context/adr/001-hosted-agentcore-with-local-mode.md), [ADR 003 — Bedrock Nova over Claude](context/adr/003-bedrock-nova-over-claude.md), [ADR 005 — Gateway tools via Lambda targets](context/adr/005-gateway-tools-via-lambda-targets.md).
- **Per-increment Tutorials** — [`/awos:tutorial`](.awos/commands/tutorial.md) → [`context/tutorials/`](context/tutorials/README.md). A narrative learning artifact for each completed increment, teaching the concepts it introduced (depth-first, Socratic), with concept dedup against earlier tutorials so nothing is re-taught. See the **[tutorials index](context/tutorials/README.md)** for the reading order; start with [Tutorial 001 — playable skeleton](context/tutorials/001-playable-skeleton/tutorial.md), or jump to [Tutorial 004](context/tutorials/004-robust-vote-input-validation/tutorial.md) for a standalone read on a LangGraph interrupt/resume gotcha.

How they relate: a **CR** records *what* changed in the requirements and *why*; an **ADR** records *how* an architectural question was settled; a **tutorial** explains, after the fact, *how the result works* so a returning reader (or a curious dev) can learn the pattern without reading every commit.

### Also a spec-driven coding experiment

Beyond the LangGraph/AgentCore content, Graphia was a deliberate experiment in **how far the usually-tedious lifecycle paperwork can be automated** when an AI assistant drives a spec-driven workflow. The verdict — at least for a greenfield project like this one — is that the "bureaucratic" steps that teams routinely skip or defer came out roughly **90% automated**:

- **ADRs and CRs** fall out of the moment a decision or a requirement actually changes, captured in the same conversation that made the change — rather than being reconstructed weeks later (when the rationale has evaporated) or never written at all.
- **Documentation and per-increment tutorials** are the clearest win. It's normally hard to remember every gotcha worth writing down — the subtle interrupt/resume contract, the IAM permission that was the *real* root cause behind a flat trace tree, the bug a mocked test structurally couldn't catch. Generated alongside the work, from the spec + the diff + the surprises hit on the way, the tutorials capture those lessons while they're still fresh.
- **The project timeline** ([`context/project-timeline.md`](context/project-timeline.md)) stays current as a side effect of the same flow, instead of being a stale doc nobody updates.

The artifacts in [`context/`](context/) aren't decoration after the fact — they're the running record the workflow produced as the code was written. That's the experiment: keep the engineering legible without the legibility being a chore.

---

## Status

Graphia is built incrementally and is an active personal reference project. Phases 1–2 (playable skeleton + hosted AgentCore deployment) are complete; later phases (long-term cross-game memory, AI provider flexibility, richer night resolution, personas) are tracked in [`context/product/roadmap.md`](context/product/roadmap.md).

*The `adventure.py`, `support_agent.py`, `main.py`, and `TUTORIAL.md` files at the repo root predate Graphia and are unrelated learning artifacts kept for reference.*
