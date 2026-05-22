# Functional Specification: Play-As-Role via Environment Variable

- **Roadmap Item:** Developer affordance — adjacent to Phase 5 (Setup Flexibility) but narrower: pins the human's role within the existing fixed lineup. Does not change role counts.
- **Status:** Draft
- **Author:** Alexey Tigarev

---

## 1. Overview and Rationale (The "Why")

Today a player's role (Mafia or Law-abiding Citizen) is dealt at random from the fixed seven-card lineup — two Mafia, five Law-abiding. There's no way to choose which side you play; you take whatever the shuffle hands you. For someone repeatedly exercising the game — especially the author testing Mafia-only flows (night kills, private intros) versus Citizen-only flows — the random draw is friction: you relaunch until the seed happens to deal you the side you want.

This adds an optional setting, the `GRAPHIA_ROLE` environment variable, that pins the human's role for the session. It is read the same way as the other Graphia settings (from the environment / `.env`), which means it composes cleanly with the existing `make play` / `make play-remote` launch flow without threading a new command-line flag through the Makefile. The rest of the lineup fills the remaining cards as usual, the overall composition is unchanged, and (when a seed is set) the full assignment stays reproducible. When the variable is unset, behaviour is exactly as today — a random draw.

**Success looks like:** A developer launches with `GRAPHIA_ROLE=mafia` (via `.env`, an inline env var, or a `make play` passthrough), and the role-reveal at game start always tells them they are a Mafioso — no relaunch lottery. Setting `GRAPHIA_ROLE=law-abiding` always seats them on the Law-abiding side. Leaving it unset deals randomly, exactly as before.

---

## 2. Functional Requirements (The "What")

### 2.1 `GRAPHIA_ROLE` pins the human's role

- **As a** developer launching Graphia, **I want to** set `GRAPHIA_ROLE` to `mafia` or `law-abiding` to choose which side I play, **so that** I can exercise a specific role without relaunching until the random deal cooperates.
  - **Acceptance Criteria:**
    - [ ] With `GRAPHIA_ROLE=mafia`, the human is always seated as a Mafioso; the role-reveal message at game start reads "Your role is Mafia." (matching the existing reveal wording).
    - [ ] With `GRAPHIA_ROLE=law-abiding`, the human is always seated as a Law-abiding Citizen; the reveal reads "Your role is Law-abiding Citizen."
    - [ ] The value is case-insensitive: `MAFIA`, `Mafia`, `mafia` all work identically.
    - [ ] Only `mafia` and `law-abiding` are accepted. Any other value (e.g. `citizen`, `villain`, empty string) makes the program refuse to start and print an error that names the two valid choices.

### 2.2 Composition and the rest of the lineup are unchanged

- **As a** developer, **I want** forcing my role to NOT change the overall game balance, **so that** the game I'm testing is the same game everyone else plays — just with my seat pinned.
  - **Acceptance Criteria:**
    - [ ] The total lineup is still two Mafia and five Law-abiding, seven players, regardless of the setting.
    - [ ] When `GRAPHIA_ROLE=mafia`, the human takes one Mafia seat and the remaining six players are dealt the other one Mafia + five Law-abiding cards.
    - [ ] When `GRAPHIA_ROLE=law-abiding`, the human takes one Law-abiding seat and the remaining six players are dealt two Mafia + four Law-abiding cards.

### 2.3 Reproducibility under a seed

- **As a** developer, **I want** `GRAPHIA_ROLE` combined with `GRAPHIA_SEED` to produce the same full game every time, **so that** I can reproduce a specific scenario deterministically.
  - **Acceptance Criteria:**
    - [ ] With the same seed and the same `GRAPHIA_ROLE`, every launch produces the identical role assignment for all seven players (not just the human).
    - [ ] Changing only the seed (same `GRAPHIA_ROLE`) reshuffles the non-human players' roles while keeping the human's pinned role fixed.

### 2.4 Default behaviour unchanged when the variable is unset

- **As a** player who doesn't set the variable, **I want** the role deal to work exactly as it does today, **so that** the normal game is untouched.
  - **Acceptance Criteria:**
    - [ ] With `GRAPHIA_ROLE` unset, all seven roles are dealt at random (seeded by `GRAPHIA_SEED` as today), with the human's role unconstrained.
    - [ ] No new prompt, message, or visible change appears in the default (unset) launch.

### 2.5 Convenient launch via `make play`

- **As a** developer who launches through the Makefile, **I want to** set the role without editing `.env` every time, **so that** trying a role is a one-liner.
  - **Acceptance Criteria:**
    - [ ] `make play ROLE=mafia` (and `make play-remote ROLE=mafia`) launches the game with the human seated as Mafia; `ROLE=law-abiding` seats them Law-abiding.
    - [ ] Omitting `ROLE` from the `make play` invocation falls back to whatever `GRAPHIA_ROLE` is in `.env` (if anything), and if that too is unset, to the random default.

---

## 3. Scope and Boundaries

### In-Scope

- A single setting, `GRAPHIA_ROLE`, read from the environment / `.env`, accepting exactly `mafia` or `law-abiding` (case-insensitive).
- Pinning the human's role while preserving the fixed 2-Mafia / 5-Law-abiding composition.
- Deterministic full assignment when combined with a seed.
- An error-and-refuse-to-start path for invalid values.
- A `ROLE=` passthrough on the `make play` / `make play-remote` targets.

### Out-of-Scope

- A `--role` command-line flag (deliberately not added — the env var composes better with the Makefile launch flow).
- **Configurable role counts** (asking how many Mafia / Citizens) — that's Phase 5 (Setup Flexibility), a separate spec.
- Forcing roles for AI players, or forcing a *specific other player* to be Mafia.
- Any in-game UI for choosing a role (this is launch-time only).
- New roles beyond Mafia / Law-abiding (Phase 8 Expanded Roster).
- Changing the role-reveal message wording or the private Mafia-intro flow.
- All other remaining roadmap items.
