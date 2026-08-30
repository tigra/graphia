# Tasks: Bedrock Claude (Haiku) Provider (Spec 035)

**Verification-spike-first** (functional spec / ADR-012): **Slice 1** wires the provider minimally and proves the local path reaches Claude + round-trips structured output; **Slice 2** builds it out (per-tier overrides, clear errors, docs); **Slice 3** proves the **deployed-runtime** path (the historically brittle part). A third provider alongside Nova/Ollama; config-selected (Nova stays default) — **no ablation flag** (a provider choice, not a gameplay toggle).

> **Shared surface with spec 034:** both edit `llm.py` (`_resolve_provider`/the Bedrock branch) + `config.py` (provider parse / model config vs persona flags). Combine on merge (the 028/030 pattern), not blind parallel edits. The offline code/tests parallelize; the **spike runs (Slice 1 last task, Slice 3) are developer-run** (real Bedrock / live deployment) and deferred.

Functional spec: `./functional-spec.md` · Technical considerations: `./technical-considerations.md`

---

- [x] **Slice 1: Select Claude locally and prove the path**
  - [x] Minimal provider wiring (tech-spec §2 B): in `src/graphia/config.py` extend the `GRAPHIA_LLM_PROVIDER` parse to accept **`bedrock-claude`** (keep `bedrock`/`ollama`; update the error message) and add per-tier model-id config (`large_model`/`small_model` via `GRAPHIA_LARGE_MODEL`/`GRAPHIA_SMALL_MODEL`) with the **documented Claude Haiku defaults** under `bedrock-claude` (Nova ids stay the `bedrock` defaults). In `src/graphia/llm.py` add a `case "bedrock-claude"` to `_resolve_provider()` returning `ChatBedrockConverse(model=<resolved id>, region_name=config.aws_region, temperature=…)`, factoring the Bedrock construction so Nova + Claude share it (Nova behavior identical). **Verify the exact Claude Haiku Bedrock model id / `us.` inference profile at implementation** — do not hard-code unverified. **[Agent: langgraph-agentic]**
  - [x] Tests (offline, mocked): config parse accepts `bedrock-claude` and resolves the documented default ids (and honors the `GRAPHIA_LARGE_MODEL`/`GRAPHIA_SMALL_MODEL` overrides); `_resolve_provider()` builds Claude-configured `ChatBedrockConverse` instances with **no live call** (assert model ids/region at the boundary); Nova/Ollama resolution is **unchanged** (regression); `safe_llm` keeps the suite off real Bedrock. Full `uv run pytest -q` green. **[Agent: testing]**
  - [x] Local verification spike (developer-run, real Bedrock): a `make claude-spike` target that, with `GRAPHIA_LLM_PROVIDER=bedrock-claude` + live creds, calls `get_large()` once and **round-trips one structured output** (e.g. `Ballot`/`DayAction` — confirm the flat schemas round-trip on Claude via Bedrock Converse). Proves the path + the structured-output contract before build-out. _(developer-run; real Bedrock.)_ **[Agent: langgraph-agentic]**

- [x] **Slice 2: Robust selection — overrides, clear errors, docs**
  - [x] Clear feedback (tech-spec §2 C): a Claude preflight mirroring `preflight.run_ollama_preflight` — verify credentials / model access / region before games and raise `SystemExit` with **plain-language, actionable** messages (no stack trace) for the missing-or-expired-creds, missing-model-access, and wrong-region cases (map the boto3/Bedrock `AccessDenied`/`UnrecognizedClient`/`ValidationException` families). Mid-game unusable outputs continue via the **existing** retry-then-fallback safety nets (no new mechanism). **[Agent: langgraph-agentic]**
  - [x] Tests (offline, mocked): the preflight maps each representative boto3 error family to its plain message (no stack trace) on a faked client; per-tier overrides honored; switching among the three providers resolves the right instances. Full `uv run pytest -q` green. **[Agent: testing]**
  - [x] Docs: a quickstart (README / docs) for selecting each provider (`GRAPHIA_LLM_PROVIDER` = `bedrock` | `bedrock-claude` | `ollama`), naming the Claude **default model ids** and the per-tier override env vars. **[Agent: langgraph-agentic]**

- [x] **Slice 3: Deployed-runtime verification (developer-run, live AWS)**
  - [x] Deployed spike (tech-spec §2 D): with the hosted AgentCore runtime configured for `bedrock-claude`, run a full game on it and confirm from the **runtime's CloudWatch telemetry** that Claude served the calls (not inferred locally); apply any **IAM/model-access (terraform)** change needed for the runtime role to invoke the Claude model. The historically brittle path — proven on the deployment. _(developer-run; live AWS + deployment.)_ **[Agent: terraform-aws]**

- [x] **Slice 4: Follow-through discovered at verification (2026-08-30)**
  - [x] Eval harnesses accept the Claude provider: add `bedrock-claude` to the provider vocabulary of `blunder_eval` (`Provider` / `PROVIDERS`) and `persona_bench` (argparse `choices`), so a measured run can be taken on Claude. Also wire `run_claude_preflight` into the eval boot so an unreachable model stops the run with a plain message **before** game 1, rather than failing partway through a paid batch; Nova keeps its existing no-preflight story. Tests: both CLIs accept the arm; the preflight runs, runs before any game, and aborts without playing when it fails; the Claude arm never runs the Ollama preflight and Nova never runs the Claude one. **[Agent: ai-quality-eval]**
  - [x] Local runs leave proof of the provider: stamp the `app_start` trace record with provider, both resolved tier model ids, and the endpoint locating them (AWS region for the Bedrock arms, base URL for Ollama, the irrelevant one null), plus `remote_mode`. Add `config.resolved_tier_models()` as the single answer to "which model is in play?". Credentials and the AWS profile name are deliberately excluded, with a test that plants both and asserts neither reaches the record. **[Agent: python-backend]**

---

> **Slice 1 spike result (2026-08-29, `make claude-spike`, live Bedrock, `us-east-1`).** **PASS.**
>
> Built the `make claude-spike` target (`src/graphia/tools/claude_spike.py`) and ran it against real Bedrock. It forces `GRAPHIA_LLM_PROVIDER=bedrock-claude` for the process (so it proves the *configured* path, not a hand-built client), runs the Slice-2 Claude preflight, then round-trips every flat schema through `get_large().with_structured_output(...)`:
>
> | schema | result | latency | parsed |
> | --- | --- | --- | --- |
> | `Ballot` | PASS | 1.01s | `Ballot(yes=True)` |
> | `DayAction` | PASS | 1.36s | `DayAction(kind='speak', text='…', target_id=None)` |
> | `Pointing` | PASS | 0.76s | `Pointing(target_id='Dara')` |
> | `Roster` | PASS | 0.79s | `Roster(names=['Eleanor', 'Marcus', 'Beatrice'])` |
>
> **Both open questions are answered.** (1) The **model id / inference profile is real**: `us.anthropic.claude-haiku-4-5-20251001-v1:0` resolves and the preflight reports the model reachable — the `VERIFY-AT-RUNTIME` note in `config.py` is now discharged for the local path (comment updated in place). (2) The **flat-schema contract holds on Claude**: the flatness constraint was established against *Nova* (Bedrock Converse rejecting discriminated unions), and Claude-via-Converse honours the same shapes — including `DayAction`, the one with the mutual-exclusion validator, which returned a well-formed `speak` action with `target_id=None`.
>
> Slice 2's preflight was exercised on the live path it was written for, not just against faked boto3 errors.
>
> **Still unproven: the deployed runtime (Slice 3).** This spike is local-only. The historically brittle part — whether the hosted AgentCore runtime's own role can invoke the Claude model, confirmed from its CloudWatch telemetry rather than inferred locally — is untouched by this result.
>
> No test was added for the spike harness itself, matching the `ollama_smoke` precedent (developer-run real-model harnesses live outside the mocked suite).

> **Slice 3 result (2026-08-30, deployed AgentCore Runtime, live AWS).** **PASS.**
>
> Two Terraform changes were required and are committed (`5422b57`): the Runtime had **no provider setting**, so a first remote game on 2026-08-30 silently ran on Nova (its own logs: 180 `amazon.nova-pro-v1:0` + 5 `amazon.nova-lite-v1:0`, zero Claude) — and the role's invoke scope was `amazon.nova-*` only. Both are now opt-in via `LLM_PROVIDER=bedrock-claude`.
>
> After redeploying, the live Runtime config carries `GRAPHIA_LLM_PROVIDER=bedrock-claude` with both tiers pinned to `us.anthropic.claude-haiku-4-5-20251001-v1:0`, and a full game ran on it:
>
> | window | model | calls |
> | --- | --- | --- |
> | 08:53–09:06 (before) | `amazon.nova-pro` / `nova-lite` | 180 / 5 |
> | **09:16–09:24 (Claude game)** | **`us.anthropic.claude-haiku-4-5-20251001-v1:0`** | **136** |
>
> **Zero Nova calls in the Claude window** (the two games are cleanly separated by minute; an initial `--since` read overlapped them and was disambiguated by per-minute breakdown). The disappearance of `nova-lite` corroborates: both tiers went to Claude.
>
> **Game completed, not partial** — node traversal covers `assign_roles`, `generate_personas`, `first_night_mafia_intros`, 24 × `day_turn`, 10 × `collect_votes`, 4 × `day_round_reflect`, `check_win_night`/`check_win_day`, `end_screen`; it resolved to `"winner": "mafia"` and exited on `runtime invocation done`.
>
> **No errors of any kind.** Precise greps for `AccessDenied`, `ThrottlingException`, `ValidationException`, `UnrecognizedClient`, `ExpiredToken` return nothing across the full window. This is the headline: ADR-012 predicted the `us.` profile's non-deterministic three-region routing would reopen ADR-003's Marketplace/IAM friction — the three-region grant plus per-region model access held, with not one denial.

> **Local full-game verification (2026-08-30, `GRAPHIA_LLM_PROVIDER=bedrock-claude make play`).** **PASS** — closes functional-spec §2.2's local-play criterion, the last one outstanding.
>
> The local JSONL trace records graph-stream deltas only — **no provider, no model id, no winner** — so it cannot itself prove which model served the game. Evidence was taken from **Bedrock's own `Invocations` metrics** instead, in 5-minute buckets to keep the local run clearly apart from the remote one earlier the same hour:
>
> | window (UTC) | run | `us.anthropic.claude-haiku-4-5` | Nova |
> | --- | --- | --- | --- |
> | 09:15–09:25 | deployed runtime | 60 | 0 |
> | **09:45–10:10** | **local `make play`** | **60** | **0** |
>
> Two distinct, non-overlapping clusters. The game completed: node traversal covers `generate_roster`, `generate_personas`, `first_night_mafia_intros`, 28 × `day_turn`, 6 × `collect_votes`, 4 × `mafia_point`, 4 × `day_round_reflect`, two Nights (`night_open` / `resolve_night_kill` / `check_win_night`), `resolve_vote`, `reveal_role`, and `end_screen` with a `winner`.
>
> *(Aside: `ValidationException — on-demand throughput isn't supported` entries for the bare, un-prefixed `anthropic.claude-haiku-4-5-…` id appear in the append-mode log, but date from **2026-04-23 and 2026-05-13** — earlier Claude experiments, and precisely the constraint ADR-012 later documented. Nothing from today.)*
>
> **Observability gap worth noting:** because the local trace carries no model identity, confirming which provider served a local game requires an out-of-band CloudWatch query. A provider/model echo at boot in the trace log would make local runs self-evidencing, the way the deployed runtime already is.

> **Slice 4 rationale — why tasks were added after the spec was verified (2026-08-30).**
>
> **A promise the acceptance criteria never tested.** This spec's rationale commits to Claude for **evaluations** in four separate places — the overview ("run games and evaluations on a stronger, near-frontier model"), the scope line ("both for local play/evaluation"), the desired outcome ("play *or evaluate* a full game on it"), and §2.1's own user story ("so that games **and evals** run on a stronger cloud model without any code change"). Yet no slice built it, **no acceptance criterion tested it**, and verification consequently passed 19/19 with the capability absent: `blunder_eval.PROVIDERS` and `persona_bench`'s argparse `choices` were both still `("ollama", "bedrock")`.
>
> It was worse than merely missing. Both harnesses **force** `GRAPHIA_LLM_PROVIDER` from `--provider`, while `BedrockProvider` resolves its model from `config.large_model` — which honours `GRAPHIA_LARGE_MODEL`. So `--provider bedrock` with a Claude id in the environment really invokes Claude while the ledger records `provider: bedrock`. A mislabelled record on the field runs are grouped and compared by is a worse failure than being blocked outright, because it looks plausible. §2.1's missing acceptance criterion (added and ticked 2026-08-30) closes the promise/criteria gap.
>
> **An observability gap the verification itself exposed.** Confirming §2.2's local-play criterion was only possible via an out-of-band CloudWatch `Invocations` query, because the local trace recorded graph-stream deltas and nothing about the model — while the deployed Runtime had been self-evidencing from its own logs all along. A local game that cannot say which model played it makes every future provider comparison unfalsifiable from the repo alone, so the boot stamp was added rather than left as a note.
>
> Neither item reopens the verified result: all 19 original criteria genuinely passed on the evidence recorded above. Slice 4 records work performed **after** that verification, and the added §2.1 criterion is ticked against the capability as it now exists.
