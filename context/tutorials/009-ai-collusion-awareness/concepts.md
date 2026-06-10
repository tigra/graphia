---
spec: 009-ai-collusion-awareness
spec_title: AI Collusion Awareness (and how a one-line prompt was actually chosen)
introduced_on: 2026-06-10
---

# Concepts introduced in this increment

## Prompt engineering

- **Prompt-priming backfire** (`prompt-priming-backfire`) — A system-prompt line meant to make AIs *notice* copycat messages instead primed the model to *produce* and fixate on repeated phrasing; telling a model to attend to a pattern can reproduce that pattern.
- **Anti-parrot self-instruction** (`anti-parrot-self-instruction`) — The working fix instructs the AI's *own* output ("say something new; don't echo a point already made") rather than telling it to watch others — and the experiment showed that merely *removing* the bad line barely helped: you must instruct against the behavior, not just stop priming it.

## LLM-behavior evaluation

- **Real-LLM eval, make-gated outside the suite** (`real-llm-eval-make-gated`) — A `make`-gated harness that deliberately reaches the real gameplay model to measure dialogue quality, opting out of the mocked `safe_llm` net that forbids Bedrock in `pytest`.
- **Paired-seed LLM A/B** (`paired-seed-llm-ab`) — Reusing one fixed seed set across every condition pins the role deal and the RNG *stream*, but because LLM-driven votes and night-kills decide who's alive — and when each mechanical draw is even consumed — nothing past the deal is reliably matched; so seeding is a variance-reduction technique (correlating paired runs), not an exact matched-pairs design.
- **Name-masked similarity metric** (`name-masked-similarity`) — Replacing player names with a `<NAME>` token before computing similarity makes template echo ("X's behavior is suspicious" / "Y's behavior is suspicious") register as the near-duplicates they are.
- **Length-cap confound control** (`length-cap-confound-control`) — Capping each game at a fixed number of speeches keeps the repetition *rate* comparable, since longer/less-decisive games accumulate more echo for reasons unrelated to the change under test.
- **Bootstrap CIs + paired-test ranking** (`bootstrap-paired-ranking`) — Per-game metrics are aggregated into bootstrap confidence intervals and a paired bootstrap difference vs the baseline condition (with Holm correction), so fixes are ranked with stated uncertainty instead of bare point estimates.
- **In-process factor injection** (`in-process-factor-injection`) — Each experiment condition (window / prompt / temperature) is applied by `setattr` on module globals and rebuilding the LLM singleton — no source edits and no git-checkout between conditions.
- **Rigor reverses the noisy pilot** (`rigor-reverses-noisy-pilot`) — A quick n=2 A/B ranked the eventual winning fix a *failure*; adding pairing, sample size, name-masking and a length cap flipped the verdict — the methodology earned its cost by overturning a wrong conclusion.

## Diagnosis

- **GenAI trace archaeology** (`genai-trace-archaeology`) — Recovering the actual model prompts and completions from CloudWatch GenAI-observability spans (the OpenInference `ChatBedrockConverse` spans' tool-call arguments) when the local stream-trace log records only node keys, not speech text.
