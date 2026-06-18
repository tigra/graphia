---
spec: 016-ai-character-personas
spec_title: AI Character Personas
introduced_on: 2026-06-18
---

# Concepts introduced in this increment

## Design / domain model

- **Two-layer deception persona** (`two-layer-deception-persona`) — A Mafioso's character is modelled as two layers — a `public_persona` (the cover legend it performs) and a `true_self` (its real, Mafioso-aware backstory) — while a Citizen has a single honest persona; this double identity is the spine the whole feature is built around.

## State & persistence

- **Persona as transient per-player state** (`persona-transient-player-state`) — Personas live on `PlayerState` for the game's lifetime and are deliberately *not* persisted across sessions (no AgentCore Memory / no `DiaryStore`), unlike diaries or career stats — they're game-scoped flavour, not durable data.
- **`dataclasses.replace` for forward-proof state rebuilds** (`dataclasses-replace-rebuild`) — Nodes that mutate a player rebuild it with `dataclasses.replace(player, <changed fields>)` instead of a full `PlayerState(...)` reconstruction, so a newly-added field (here `persona`) carries over automatically instead of being silently dropped at every rebuild site.
- **Register a new state type on the checkpoint serde allow-list** (`serde-allowlist-new-state-type`) — A new dataclass nested in `GameState` must be added to the checkpoint serializer's `allowed_msgpack_modules`, or it round-trips through the checkpoint as a plain `dict` (not the typed object) — the allow-list is opt-in, so every new state type pays this tax.

## Generation

- **Role-tailored creative generation with a never-block floor** (`role-tailored-persona-generation`) — Each AI player's persona is produced by a per-actor heavyweight-LLM call with a *role-specific* prompt (Citizen = one honest persona; Mafioso = legend + true self), wrapped in invoke → one retry → deterministic fallback so a flaky or absent model never blocks game setup.

## Prompt design / knowledge boundary

- **Owner-private secret in the prompt** (`owner-private-secret-in-prompt`) — A Mafioso's `true_self` (and the "stay in cover" instruction) is injected *only* into that Mafioso's own Day-speech prompt — never broadcast, never threaded into another player's prompt — so the deception is playable without leaking allegiance; the persona is the voice layer atop the spec-013 role grounding.
- **Hidden during play, revealed at end** (`hidden-then-revealed-at-end`) — Personas are never shown as profiles during play (only *felt* through how each character talks); the explicit personas are revealed publicly only at game end, contrasting each Mafioso's performed legend with its true self.

## Testing

- **Seed module-global RNG to de-flake an order-dependent test** (`seed-rng-to-deflake-order-dependent-test`) — A real-driver test whose trajectory depends on cumulative module-global `random` state is made order-independent by calling `random.seed(...)` at the test's start (the architecture §6 sanctioned pattern), so adding unrelated tests elsewhere can't flip it red.
