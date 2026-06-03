# Graphia Tutorials

Per-increment learning tutorials for [Graphia](../../README.md). Each one covers a single completed spec increment — teaching **only** the concepts that increment newly introduced, **depth-first** (deepest idea first, decorations last) and **Socratic** (pose a design question → name the framework feature that answers it → apply it with a real snippet). Concepts already taught by an earlier tutorial are *referenced, not re-taught*, so the set reads cleanly front to back.

Each tutorial ships with a companion `concepts.md` — a ledger of the concepts it introduced (human-readable title + a stable kebab-slug). That ledger is the dedup mechanism: a later tutorial reads all earlier ledgers and skips anything already covered.

> These artifacts are produced with the [`/awos:tutorial`](../../.awos/commands/tutorial.md) skill (an extension to the [AWOS](https://github.com/provectus/awos) workflow). See the [project timeline](../project-timeline.md) for how the increments unfolded and the [roadmap](../product/roadmap.md) for what's next.

---

## Read in order

| # | Tutorial | What you'll learn | Concepts | Prereqs |
|---|----------|-------------------|:--------:|---------|
| 001 | [**Playable Skeleton**](001-playable-skeleton/tutorial.md) | The core LangGraph paradigm — typed state with field-level reducers, conditional-edge routing, replay-safe `interrupt()` + `Command(resume=…)`, streaming super-step updates, a per-thread SQLite checkpointer, flat-Pydantic structured LLM output, and the Textual TUI. The foundation everything else builds on. | 20 | none |
| 002 | [**Hosted AgentCore Deployment**](002-hosted-agentcore-deployment/tutorial.md) | Taking the *same* graph to AWS Bedrock AgentCore — the managed-container Runtime, a Gateway-fronted diary tool surface, AgentCore Memory, Terraform-in-a-container, a mode-agnostic local/remote driver, observability trace trees, and the failure modal. | 38 | 001 |
| 004 | [**Robust /vote Input Validation**](004-robust-vote-input-validation/tutorial.md) | The LangGraph interrupt/resume-**pump contract**: why a node must `interrupt()` at most once per execution, the driver "empty `next` masks a pending interrupt" gotcha that ended a game on a typo, re-prompting via a state channel + conditional-edge loop, and testing through the *real* driver. A great standalone read. | 5 | 001, 002 |
| 005 | [**Play-As-Role via Environment Variable**](005-play-as-role/tutorial.md) | The project's **determinism posture as policy** — `GRAPHIA_ROLE` to pin the human's side (pop-then-shuffle that preserves the lineup by construction), pinning roles in tests via `setenv` instead of magic seed values, monkeypatching the shuffle helper for a fixed order, and the full retirement of `GRAPHIA_SEED` from production. | 9 | 001, 002 |
| 006 | [**Long-Term Cross-Game Memory & Career Stats**](006-cross-game-career-stats/tutorial.md) | **Event sourcing on AgentCore Memory's two tiers** — per-action events as the source of truth, a long-term record as their `fold`ed consolidation — with one shared pure `fold` guaranteeing local↔remote parity, a self-managed strategy + SNS→Lambda consolidation pipeline (with `games_folded` idempotency), `partial`-based service injection behind a shared graph assembler, and a loud-fail posture. Opens with a candid note that the pipeline is *deliberately over-engineered* for a stats counter. | 16 | 001, 002 |

*(Tutorial **003** is intentionally skipped — Spec 003 "Reliable Game Exit Controls" shipped without a tutorial, and that index is left open rather than renumbering.)*

---

## Also here

- [`002-hosted-agentcore-deployment-v2/`](002-hosted-agentcore-deployment-v2/tutorial.md) — a **historical interim draft** of Tutorial 002 covering Slices 1–4 plus the Nova model switch, written *before* the ADR-005 pivot to Lambda-target Gateway tools. Superseded by the final Tutorial 002; kept as a record of the pre-pivot shape. Read 002 (final) instead unless you're specifically curious about the runtime-embedded-tools era.

---

## How a tutorial is structured

1. **Overview** — names the central technology/paradigm before any diagram.
2. **Concepts already covered** — prior-tutorial concepts this increment re-uses, linked back.
3. **What's new this increment** — an index of the new concepts (titles only), each linking down to where it's taught.
4. **Diagram** — a mermaid diagram when the increment changes something structural.
5. **Walkthrough** — the lesson: 5–8 sections, deepest concept first, each pose → present → apply.
6. **Try it** / **Where to go next** — hands-on pointer + the next thing to read.
