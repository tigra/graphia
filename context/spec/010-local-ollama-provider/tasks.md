# Tasks: Local Ollama Provider (Spec 010)

Vertical slices for [spec 010](./functional-spec.md) per its
[technical-considerations](./technical-considerations.md), implementing
[ADR 009](../../adr/009-pluggable-llm-provider-abstraction.md) (provider
abstraction) and [ADR 010](../../adr/010-anthropic-compatible-ollama-protocol.md)
(Anthropic-compatible `/v1/messages` protocol). Each slice leaves the app
runnable; offline verification is `uv run pytest -q` (the `safe_llm` net patches
`get_large`/`get_small` *above* the provider branch, so the suite never reaches
a real provider). Slice 5's real-Ollama smoke is the **ADR-010 gate** and needs
Ollama + models on the host — the user runs that live step. Agents:
`langgraph-agentic` (abstraction + provider client), `python-backend` (config,
preflight, deps, docs), `testing` (suite).

- [x] **Slice 1: Provider abstraction with Bedrock as its first implementation (pure refactor — zero behavior change)**
  - [x] In `src/graphia/llm.py`, introduce the ADR-009 abstraction: an abstract `LLMProvider` with `large()` / `small()` tier accessors returning a structured-output-capable LangChain `BaseChatModel`, and a `BedrockProvider` implementation wrapping today's two `ChatBedrockConverse` instances (same model ids, region, temperatures). `get_large()` / `get_small()` delegate to the active provider (only `bedrock` exists for now), preserving today's lazy singleton caching. **No call-site changes** in `nodes/setup.py` / `nodes/night.py` / `nodes/day.py`; the public `get_large`/`get_small` names keep working so `tests/conftest.py`'s `safe_llm` and every fixture patch target stays valid. **[Agent: langgraph-agentic]**
  - [x] Run `uv run pytest -q`; confirm the full suite is green with zero test edits — the proof the refactor changed nothing observable. **[Agent: testing]**

- [x] **Slice 2: Provider selection in config, with the contradiction guards (still Bedrock-only behavior)**
  - [x] In `src/graphia/config.py`, add the four env-driven fields per tech-spec §2.3: `GRAPHIA_LLM_PROVIDER` (`bedrock` default | `ollama`), `GRAPHIA_OLLAMA_BASE_URL` (default `http://localhost:11434`), `GRAPHIA_OLLAMA_LARGE_MODEL`, `GRAPHIA_OLLAMA_SMALL_MODEL` (defaults = the recommended models, finalized in Slice 5). Validate the provider value with a clear `SystemExit` on a typo (mirroring `GRAPHIA_ROLE`), and reject `remote_mode` + `ollama` with the plain-language contradiction message (tech-spec §2.3; functional-spec §3 — Ollama is local-only). Wire `get_large`/`get_small`'s provider selection to the config field (an unknown value can no longer reach the factory). **[Agent: python-backend]**
  - [x] Add config unit tests: default is `bedrock` with behavior unchanged; `ollama` parses the base-url/model fields; an invalid provider value exits with the clear message; `GRAPHIA_REMOTE=1` + `ollama` exits with the contradiction message; no test reaches a network. Run `uv run pytest -q`; suite green. **[Agent: testing]**

- [x] **Slice 3: The Ollama provider — Anthropic-compatible client behind the abstraction**
  - [x] Add the dependency: `uv add langchain-anthropic` (pin per project convention; pulls the `anthropic` SDK). Do **not** add `langchain-ollama` / `langchain-openai` — those are the ADR-010 fallbacks, only added if Slice 5's gate forces a switch. **[Agent: python-backend]**
  - [x] Implement `OllamaProvider` per tech-spec §2.1–2.2: `large()` / `small()` return `ChatAnthropic` instances pointed at the configured Ollama base URL (`/v1/messages` surface), dummy api-key, per-tier model from config, tier temperatures preserved, and an explicit `max_tokens` cap (Anthropic Messages requires it). Selecting `GRAPHIA_LLM_PROVIDER=ollama` now routes all four AI surfaces (day speech, night pointing, ballots, roster name-gen) through the local client with **no AWS credentials read**. **[Agent: langgraph-agentic]**
  - [x] Add offline provider tests (construction only, no network): with provider `ollama`, `get_large()`/`get_small()` yield `ChatAnthropic` configured with the expected base URL, model names, and `max_tokens`; with `bedrock`, still `ChatBedrockConverse`; singletons cache per provider. Extend nothing in `safe_llm` (the patch surface is unchanged). Run `uv run pytest -q`; suite green. **[Agent: testing]**

- [x] **Slice 4: Fail-fast preflight — plain-language errors before the TUI starts**
  - [x] Implement the boot preflight per tech-spec §2.4 (project posture: fail fast before the TUI): when provider is `ollama`, check the base URL is reachable and both configured models are installed (Ollama's native `/api/tags`); on failure exit with the exact plain-language messages — unreachable → "Couldn't reach Ollama at `<url>`. Is it running? Start it with `ollama serve`."; missing model → "The model `<name>` isn't installed. Pull it with `ollama pull <name>`." — and **no stack trace** (functional-spec §2.4). Mid-game bad output stays covered by the existing retry-then-fallback helpers; no change there. **[Agent: python-backend]**
  - [x] Add preflight unit tests with a stubbed HTTP layer (no real network): unreachable server → the "Is it running?" message, exit code ≠ 0, no traceback in output; missing model → the message naming that model; both models present → preflight passes silently. Run `uv run pytest -q`; suite green. **[Agent: testing]**

- [x] **Slice 5: The ADR-010 gate — real-Ollama smoke, recommended models, and the quickstart**
  - [x] Add a `make`-gated real-Ollama smoke (mirroring the `eval-dialogue` posture — real LLM, outside `pytest`): drive one full scripted game (the eval harness's driver pattern) against the local Ollama provider and report whether every structured-output surface (`Roster`, `Pointing`, `Ballot`, `DayAction`) parsed reliably over `/v1/messages`. Try the candidate tool-capable models from tech-spec §2.6 and record which pair is reliable. **This is the ADR-010 verify-at-implementation gate**: if tool-use over `/v1/messages` proves unreliable, stop and surface it — the fallback (swap `OllamaProvider`'s client to native `ChatOllama` or OpenAI-compat, revisiting ADR 010) is a deliberate decision, not a silent switch. **[Agent: langgraph-agentic]**
  - [x] **[User-run, live]** Run the smoke on the host with Ollama serving the candidate models (`ollama pull …`); confirm a full offline game completes — no AWS, no internet — and pick the recommended large/small model pair from the results. *(Result: `qwen2.5:7b` UNRELIABLE — 40/40 DayAction tool-call failures, model answers in prose; `qwen3-coder:30b` + `qwen2.5:3b` RELIABLE — 0 failures across Roster/Pointing/DayAction, full game in 45s. Ballot not exercised (same single-field tool-call mechanism as Pointing). First run also exposed the cloud-stats leak now recorded as a deferred follow-up.)*
  - [x] Finalize the recommended-model defaults in config (Slice 2's placeholders) from the smoke results, and write the README "Play offline with Ollama" quickstart per tech-spec §2.6: install Ollama, pull the two recommended models, set `GRAPHIA_LLM_PROVIDER=ollama`, `make play`; note that AI quality depends on the local model and is not guaranteed (functional-spec §2.5). **[Agent: python-backend]**
  - [x] Run `uv run pytest -q` one final time; confirm the suite is green and the Bedrock default path is untouched end-to-end. **[Agent: testing]** *(288 passed, 1 skipped; no-env default = `bedrock` with the smoke-verified Ollama model defaults in place.)*

---

_Determinism posture unchanged (architecture §6): no new seeds or replay claims; local-model output is non-reproducible like all LLM output. The mocked suite never reaches a real provider — Slice 5's smoke is the only real-LLM step, deliberately outside `pytest` and behind `make`, consistent with the dialogue-eval tooling._

---

## Follow-ups (surfaced during Slice 5's live smoke; activated 2026-06-12)

_Live play-test note: the user's full offline game (2026-06-12) included a vote — **`Ballot` is now exercised over `/v1/messages`** (it resolved correctly), closing the one schema the scripted smoke never reached._

- [x] **Offline gate for the cloud stats/diary stores under Ollama.** The first live smoke died on `UnauthorizedSSOTokenError`: `make_career_emitter` gates on `career_memory_id` presence alone, so with a wire-env'd `.env` even an "offline" Ollama game emits career events to AgentCore Memory — violating functional-spec §2.2 ("completes without reaching any cloud service") for anyone with a deployed stack. **Approach: provider=`ollama` blanks the memory/gateway/strategy ids at config load** (offline by construction — same behavior as a fresh contributor's machine); update the README caveat accordingly. The smoke harness's in-process pops stay as defense in depth. **[Agent: python-backend]**
- [x] **Register `PlayerState` with the checkpoint serializer.** LangGraph warns on every checkpoint round-trip: `Deserializing unregistered type graphia.state.PlayerState … will be blocked in a future version`. Benign today; a future langgraph upgrade turns it into a hard failure for every game. Add `('graphia.state', 'PlayerState')` to the serializer allowlist (per the warning's `allowed_msgpack_modules` hint) at every checkpointer construction site (local `build_graph` and the Runtime's builder share assembly — find both). **[Agent: langgraph-agentic]**
- [x] **Rename the `fake_sonnet`/`fake_haiku` test fixtures to tier names.** The same Nova misnomer as the already-renamed `get_sonnet`/`get_haiku` → `get_large`/`get_small` accessors, surviving in the test infrastructure (~100 refs across conftest + ~10 test files): `fake_haiku`→`fake_small`, `fake_sonnet`→`fake_large`, `fake_sonnet_pointing`/`fake_sonnet_day` → `fake_large_pointing`/`fake_large_day`, and the Fake* classes to match. Update CLAUDE.md's test-conventions section; tutorials stay untouched (historical artifacts). **[Agent: testing]**
