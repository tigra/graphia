# Technical Specification: Reliable Answers from the Local Model

- **Functional Specification:** [`functional-spec.md`](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Alexey Tigarev
- **Trigger:** [ADR 013 — Native Ollama Structured Output over Anthropic Tool Use](../../adr/013-native-ollama-structured-output.md)

---

## 1. High-Level Technical Approach

Four independent pieces of work, in dependency order. Only the first is the client swap; the other three exist because the functional spec asks for the substitution to become *countable* and *visible*, and neither is possible today.

1. **Swap the Ollama client** from `langchain-anthropic`'s `ChatAnthropic` (Ollama's Anthropic-compatible `/v1/messages`, structured output via tool use) to `langchain-ollama`'s `ChatOllama` (native `/api/chat`, structured output via a JSON-schema `format` grammar). Pin the method at the **provider boundary**, add `num_ctx`, rename the output cap.
2. **Carry the substitution fact in the data.** Three answer kinds gain an explicit "this was substituted" marker travelling with the value — diary records, thought records (a new shape for spec 028's channel), and the AI's Day speech message. No text-matching anywhere.
3. **Count substitutions at the source**, via a small injectable sink that the seven substituting helpers notify, surfaced as a `quality.substitutions` block per answer kind.
4. **Withdraw the interim prose recovery**, and fix an instrumentation defect it exposed.

### The three facts that shape every decision below

**(a) `ChatOllama` already defaults to `method="json_schema"`** (verified, langchain-ollama 1.1.0). A bare client swap therefore *already* gets grammar-constrained decoding. **(b) `ChatBedrockConverse.with_structured_output` also accepts `method`**, defaulting to `function_calling` — so hard-coding the method at the six *shared* call sites would silently change Bedrock's mechanism and break the functional spec's cloud-parity requirement. **(c) The routing leaves an assertable wire-level trace**: under `json_schema` the bound kwargs carry `format == Schema.model_json_schema()` and no `tools`; under `function_calling` they carry `tools` and no `format`, with a different parser class.

Together these mean the provider seam is **not required for correctness today** — it is required so that the property this spec exists to guarantee cannot be silently revoked by a third-party default flip, and so that pinning it for Ollama cannot leak to Bedrock. That is the honest justification; it should not be dressed up as necessity.

**(d) Grammar-constrained decoding guarantees JSON *shape*, never *content*.** `DayAction.model_json_schema()["required"] == ["kind"]` (verified) — its mutual-exclusion rule lives in a `@model_validator` that JSON Schema cannot express, so `{"kind": "vote"}` is grammar-valid and model-invalid. `Roster`'s case-insensitive distinctness is validator-only too, and `Pointing` can return a well-formed `target_id` naming nobody. **Three of seven kinds therefore keep a live substitution path by construction.** See §3 for what that does to the acceptance criteria.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 The provider seam — where the structured-output method lives

`LLMProvider` today declares three abstract factories returning a bare `BaseChatModel`, and each of the **eight** `with_structured_output` call sites binds its own schema. There is nowhere for a per-provider method to live.

**Decision: provider-declared defaults, applied by a kwarg-merging proxy, wrapped at the provider boundary.** Contracts:

| element | responsibility |
|---|---|
| `LLMProvider.structured_output_defaults` | New non-abstract member returning a mapping. `{}` on the ABC (so both Bedrock providers inherit it and **cannot drift**); `{"method": "json_schema"}` on `OllamaProvider`. |
| `LLMProvider._build_large` / `_build_large_at_temperature` / `_build_small` | New abstract builders. The three public factories become concrete template methods that wrap the builder's result, so a future provider **cannot forget to wrap**. |
| `StructuredMethodModel` (new, in `llm.py`) | Proxy holding `(inner, defaults)`. `with_structured_output(schema, **kwargs)` delegates with `{**defaults, **kwargs}` — **caller kwargs win**, so a call site or a test can always override. `__getattr__` passthrough for everything else (the pattern `InstrumentedModel` already establishes). Exposes a documented read-only `.inner`, and a `__repr__` naming the inner class, because on an eval run the stack is two proxies deep. |
| `llm.GAMEPLAY_SCHEMAS` (new) | The schema vocabulary declared **once** as a tuple: `Roster`, `Persona`, `Pointing`, `Ballot`, `DayAction`, `Reflection`, `Diary` — **seven**, not six. Five consumers derive from or are checked against it (see §2.6, §2.7, §4). |

**Why the provider boundary and not the `get_large()` factories.** `ollama_smoke.py:96` constructs `OllamaProvider()` directly and `blunder_eval.py:2848` calls `_resolve_provider()`; both bypass the cached factories. Wrapping in the factories would leave the two harnesses whose *job* is to verify this fix measuring an **unwrapped** client. This single fact decides the placement.

**Cost, stated honestly:** the factories no longer hand back the raw vendor client, so the construction tests reach through `.inner`. **Zero of the eight call sites change**, and the ~25 node-level fakes across ~20 test files are untouched, because they patch node-module `get_large` and never see the proxy.

**Rejected:** adding a `structured(schema)` method to `LLMProvider` — it deletes the one method every fake in the suite implements (~20 files rewritten) and collapses build-and-bind, breaking the module cache. A module-level helper consulting the active provider — two sources of truth, and it would inject `method="json_schema"` onto the bare `ChatBedrockConverse` that `repetition_experiment.py:98` assigns straight into `llm._large`.

**Typing:** introduce a `StructuredModel` Protocol (`with_structured_output(schema, **kwargs) -> Runnable`) and annotate the three public factories with it. `_large: BaseChatModel | None` is **already a type lie** — `blunder_eval` assigns an `InstrumentedModel` into it. Anything exposing `with_structured_output` must keep working in that slot.

### 2.2 Context length and the output cap

| constant | change | reason |
|---|---|---|
| `_OLLAMA_DUMMY_API_KEY` | **delete** | An Anthropic-protocol artefact; `ChatOllama` needs no key. |
| `_OLLAMA_MAX_TOKENS = 1024` | **rename** to `_OLLAMA_NUM_PREDICT`, same value | `ChatOllama` has no `max_tokens` field — it is `num_predict`. **The constant is load-bearing by citation:** `config.py` derives `_DEFAULT_CONTEXT_TOKEN_BUDGET = 20000` as `(32768 − ~1.5K scaffold − ~1K completion) × 0.75`. Deleting it makes that derivation unreconstructable. Update the citation in the same change. The cap stays as a **runaway guard** — the grammar does not guarantee termination, since a JSON string can be arbitrarily long. |
| `_BEDROCK_LARGE_TEMPERATURE` / `_BEDROCK_SMALL_TEMPERATURE` | **rename** to `_LARGE_TEMPERATURE` / `_SMALL_TEMPERATURE`; all three providers read them | `OllamaProvider` hard-codes `0.7` / `0.8` today despite the constants' own comment claiming they exist so providers cannot drift. Same literals ⇒ **observable behaviour delta is exactly zero**, so no flag and no measurement consequence. |
| `GRAPHIA_OLLAMA_NUM_CTX` | **new**, default `32768`, passed per request | `config.py` currently *hopes* an operator set the server's context length and warns about an unconfigured server defaulting to 4096. The Anthropic endpoint gave Graphia no way to set it; `ChatOllama` does. Under grammar constraint a prompt that overflows the context is a hard failure, so this turns a documented hope into an **enforced invariant**. Env-driven because it is a machine-capability knob costing local KV-cache RAM — the same footing as `GRAPHIA_OLLAMA_BASE_URL`. |

`pyproject.toml`: add `langchain-ollama`, **remove `langchain-anthropic`**. After the swap nothing imports it (`ChatAnthropic` occurs only in `OllamaProvider`; `ClaudeBedrockProvider` goes through `ChatBedrockConverse`). Leaving the dependency invites a contributor to reintroduce the retired client. `uv.lock` has zero `ollama` matches, so this is a genuine `uv add` / `uv remove`.

**Verify-at-implementation:** whether Ollama's `format` applies to a thinking-capable model's reasoning segment is version-dependent. If the smoke run shows truncation, raise `num_predict` to 2048 rather than adding a knob.

### 2.3 Carrying the substitution fact — three kinds, no text-matching

The functional spec requires the marking to be **carried in the data, not inferred by comparing against the stand-in text**. That rules out the renderer-side equality the measurement layer uses, and it means three separate carriers:

| kind | carrier | shape |
|---|---|---|
| Diary | `DiaryRecord` gains `substituted: bool` | Already a `TypedDict` with `total=False`; absent reads False, so the committed transcripts and any resumed checkpoint read correctly. A `TypedDict` never reaches the msgpack ext hook, so `make_checkpoint_serde`'s allowlist is unaffected. |
| End-of-round thought | **`private_thoughts` is reshaped** from `dict[str, list[str]]` to `dict[str, list[ThoughtRecord]]`, mirroring `DiaryRecord` | The larger piece of work here. Spec 028's channel holds **bare strings** with nowhere to put a flag, so "no text-matching" forces the reshape. `ThoughtRecord(TypedDict, total=False)` carries `text` and `substituted`. |
| AI Day speech | the message's `additional_kwargs` gains `substituted: True` | Non-invasive: `additional_kwargs` already carries `private_to`, and the transcript writer already reads messages. No new channel. |

**The thought-channel reshape is the biggest risk in this spec.** Its blast radius, to be sized in the task list rather than discovered: the `_merge_private_thoughts` reducer; `_private_record_block` (one definition, four call sites) which renders thoughts as strings; `DiaryRecord.thoughts_before`, the cross-channel **cursor** — it counts thoughts, so its arithmetic is unaffected, but every site that indexes the list must now read `.get("text")`; `_append_thoughts` in the transcript writer; and spec 028's and 039's test suites. A **backward-compatible read helper** (accept a `str` or a `ThoughtRecord`, return the text) is the mitigation that keeps this from being a big-bang change, and the committed checkpoints question is moot because no checkpoint crosses sessions.

**Determination happens where the substitution happens** — the node that substitutes sets the marker. This supersedes a recorded rationale: `blunder_eval._diary_fallback_text`'s docstring rejects "having the NODE surface the fact (a flag on `DiaryRecord`, or a second return value)" as out of proportion "to avoid one private import in a measurement tool". That cost is no longer being paid for measurement — the transcript marking cannot be derived post-hoc. **Update that docstring's argument rather than quietly contradicting it.**

### 2.4 The transcript marking

Emission, in `eval_transcript.py`, following the **conditional-attribute idiom** `_diary_day_attr` already establishes (present only when true; omission over guessing — which satisfies "no entry carries that marking" structurally rather than by intent):

- `<diary player="…" day="…" substituted="true">`
- `<thought player="…" substituted="true">`
- Day speech: substituted lines only are wrapped as `<speech speaker="…" substituted="true">…</speech>`. Unsubstituted lines keep the bare `Speaker: text` shape **unchanged**, so there is no churn to the dominant line form across the 367 preserved files and no existing span test moves. This is the key concession that makes covering speech affordable.

Reading, in `eval_ledger.py` — the documented **six-touchpoint** route per kind, plus one attribute name:

- `substituted` **joins `_ATTR_NAMES`.** This departs from the `human="true"` precedent, which excluded a machine flag on the stated ground that it "is not a detail worth picking out" — and the departure is justified by that note's own test. `human="true"` restates a fact the file already carries three other ways; **`substituted="true"` is the file's only record of its fact.** `day` is the governing precedent, not `human`. Mechanical dividend: inclusion reuses the existing attribute regex, where exclusion would need a new one (as `_HUMAN_ATTR_RE` did).
- Three new body kinds — `KIND_DIARY_SUBSTITUTED`, `KIND_THOUGHT_SUBSTITUTED`, `KIND_SPEECH_SUBSTITUTED` — so each kind stays distinguishable from the others, as required. `_INLINE_CONTENT_KINDS` is keyed by tag name today; the body kind is no longer a function of the tag alone, so that lookup becomes `(tag, flag) → kind`. This stays a pure per-line decision inside `_line_spans` because the flag sits on the very tag whose body it re-kinds — no whole-file pre-pass, so **cheaper than spec 038's human-seat work**.
- `substituted="` occurs **0 times** across the 367 preserved transcript files (measured), so no historical file changes meaning.

Styling, in `ledger_viewer.py`: the palette is documented **spent** (only five theme variables clear WCAG AA on both builtin themes) and `bold` / `italic` / `underline` / `reverse` are taken; `overline` is measured-dead on Textual 8.2.4. `strike` **is** live (verified: present in `VALID_STYLE_FLAGS` *and* a field on `textual.style.Style`, surviving both conversion hops). **Recommendation: each substituted kind takes its honest twin's rule plus `text-style: strike`** — semantically "this text is void", inheriting the twin's colour so the measured contrast (7.16:1 dark / 5.31:1 light) is untouched.

**Two axes, not one**, per the rule already written into the `thought` style: the bold `true` on the tag line and the strike on the body. A terminal without SGR 9 keeps the bold flag; a reviewer skimming bodies rather than tag lines catches the strike. Both are SGR-only, so neither depends on the spent palette.

**Record in this spec that it spends the last comfortable `text-style` slot** — after `strike`, what remains is `uu` (thin support), `dim` (fails the contrast floor) and `blink` (unusable) — the way spec 038 recorded spending the last hue.

**Rejected:** rewriting the body text (e.g. a `[substituted]` prefix). The stand-in sentence **is** what that player's diary contained — it went into the next Day's prompt, the diary store, and (spec 040) the Moderator recap. Rewriting it would make the transcript disagree with state, and would hide from the reviewer that the placeholder is prose a player then read back as its own. It is also the only option that could threaten `tokenize_transcript`'s round-trip invariant.

### 2.5 Counting substitutions per answer kind

Exact-equality scoring cannot generalise: `Ballot(yes=False)` is the game's most common **genuine** answer, `Pointing`'s fallback is `random.choice(...)` returning a real id, `Roster` pads a generated family *and* trims, and `DayAction`'s stand-in is exactly the bland line a small model emits unprompted. Two of seven are scorable by equality, and the criterion says *each*.

**Decision: a substitution sink.** A new `src/graphia/substitution.py` exposing a `SubstitutionKind` StrEnum with exactly seven members (1:1 with `GAMEPLAY_SCHEMAS`), `note_attempt(kind)` / `note_substitution(kind)`, and `set_sink(fn)` / `reset_sink()`. Each of the seven helpers gains **one line on entry and one on its substitution branch** — 14 one-line additions, no signature changes, no state-shape change, no reducer, no caller threading.

- **Injectable sink, not a module global `Counter`.** Production's default sink emits a log line and accumulates nothing, so "the measurement must not reshape the thing measured" holds; the harness installs an accumulating sink and owns per-game isolation. It also survives the roadmap's **Asynchronous Day Chat**, which breaks the single-threaded premise a global counter rests on — a `contextvars` change in one file instead of a debugging session.
- **`note_substitution` owns the log line**, replacing the scattered `except Exception: pass`. One place, so the count and the log can never disagree. Keep the existing `logger.exception` call's **single** `speaker.id` argument — `test_a_store_failure_is_not_confused_with_a_model_failure` distinguishes the two failure axes by `record.args` **arity**.
- **`note_attempt` gives a real denominator**, so a kind with no opportunity (no Mafioso alive ⇒ no `Pointing`; diaries off ⇒ no `Diary`) is **absent, not zero** — stating the denominator where the diary gate infers it.
- **Zero false positives by construction:** the code that substitutes is the code that says so. **Survives a retry** without modelling it, because the substitution branch is reached *after* the retry.

Record shape — `quality.`, not `metrics.` (it judges whether a run measured what it claims, not how well the AI played), and **no Wilson band** on any of the seven (census, not sample; and a `day_diary` fan-out fails as a cluster). One nested sub-block keyed by kind rather than 21 flat keys, following the `settings.persona` / `settings.lineup` precedent:

```
quality:
  games_attempted / games_completed / games_failed_early
  substitutions:
    roster:     {rate: 0.0, count: 0, attempted: 10}
    …one row per kind, omitted entirely when attempted == 0…
  diary_fallback_rate / diary_fallback_entries / diary_entries_attempted   # pre-041 spelling, retained
  duration_seconds
```

**`METRICS_VERSION` stays at 1.** Purely additive; no detection rule changes; no existing denominator moves; the eleven `METRIC_ORDER` metrics are byte-identical. Say explicitly that **the transport changed and that is not what `METRICS_VERSION` versions** — it versions the rule set that *scores* a run. Bumping would falsely flag ~30 committed records as incomparable for a reason with nothing to do with scoring.

**Keep the flat `diary_*` trio emitting exactly as today** alongside `substitutions.diary`: six committed records carry it, the reader gates on `quality.diary_entries_attempted`, and four test files pin the no-bump assertions. Three duplicated keys buy an un-migrated read of every historical record. **Keep `score_diary_fallback` too, as an independent cross-check** — the sink's diary count must equal it, and disagreement means a substitution path forgot to signal.

Viewer: extend `_render_quality_section`, gated **per kind** on that kind's `attempted`. One new fixed table column showing the **worst kind's rate with its name** (`diary 0.50`, `—` when nothing was attempted) — because `diary_fallback_rate`'s whole job was to stop a half-treated arm reading as a result, and **it failed at that for a human scanning the table**: the disqualified row looked like every other row. `METRIC_ORDER` is untouched, so no metric cell moves. Do **not** import the enum into `eval_ledger` (the model-free viewer loads it); duplicate the seven names with a comment and pin the agreement in an offline test.

Ledger provenance: add **`provider.structured_output: json_schema | tool_use`**. Without it, the three generations — broken tool-use, interim recovery, reliable decoding — are distinguishable only by `code.commit`, i.e. only to a reader willing to run `git log`, against the ledger's principle that a record reads on its own. **This matters more than the per-kind rates**, because the interim generation and this one both read `0.00` for different reasons, and conflating an *absorbed* failure with an *absent* one is exactly what ADR 013 §5 says the withdrawal exists to prevent. Additive ⇒ no bump.

### 2.6 Withdrawing the interim recovery

One clean cut at `day.py:1788-1895` (`_DIARY_LABEL_MAX`, `_content_text`, `_recovered_diary_text`, `_diary_entry_from`); `day.py:1957` reverts to `with_structured_output(Diary)`; `import json` at `day.py:28` becomes unused. **Keep the `logger.warning` and `logger.exception`** — only the recovery goes. `tests/test_slice39_diary_prose_recovery.py` is deleted in full, and `conftest.py`'s `include_raw` handling and `Diary | BaseMessage | Exception` widening revert.

**The `logger.warning` has exactly one test in the whole suite, and it is in the file being deleted.** Re-home it onto the empty-entry path in `tests/test_slice39_diary_before_night.py`, or the withdrawal silently drops the only coverage of a line we are deliberately keeping.

### 2.7 The instrumentation defect

`_InstrumentedStructured.invoke` classifies success as `isinstance(result, self._schema)`. With `include_raw=True` the result is a **dict**, so **every diary invoke is currently booked `record_failure("non-instance result: dict")`** — `SchemaStats["Diary"].failure_rate` is 1.0 by construction, and **the ADR-013 transport gate cannot observe its own fix.** Nobody saw it because `ollama_smoke.SCHEMA_NAMES` omits `Diary` and only the `--json` report iterates the full stats map.

Fix: judge an `include_raw` mapping on `result["parsed"]` being a schema instance with `parsing_error is None`. **Ship this in the withdrawal commit even though `include_raw` is leaving** — the proxy is the measurement instrument and must not lie about a shape LangChain can legitimately return.

Smoke gate (`ollama_smoke.py`): derive `SCHEMA_NAMES` from `GAMEPLAY_SCHEMAS` (it is missing **three** — `Persona` as well as `Reflection` and `Diary`); assert `failures == 0` per schema rather than a rate under `DEFAULT_THRESHOLD = 0.20`, which is four times the rate that disqualified the arm; and **fix `_judge`, which treats a zero-attempt schema as passing** — so a gate run that never reached the diary reports RELIABLE. That is live, not hypothetical: `_run_scripted_game` returns as soon as `rounds >= max_rounds`, so the default `--max-rounds 3` can end the script **before the first Day→Night hinge**, where `day_diary` fires. Add `require_all_schemas`, name the un-exercised schemas on failure, and assert the resolved method was `json_schema`.

---

## 3. Impact and Risk Analysis

### System dependencies

**Unaffected, and stated so nobody investigates:** `runtime/graph_builder.py` needs **zero** changes — the provider resolves *below* the graph at node-call time via `get_large()`, so there is no `_assemble_graph` parameter to thread. Checkpointing is untouched (no model object is ever in `GameState`). `preflight.py` already speaks native `/api/tags` and `/api/show` against the same base URL. Remote mode never builds a `ChatOllama` (`config.py:454` rejects `--remote` + ollama) — worth a test asserting that path is unreachable, to lock ADR 001's dual-mode posture.

**No ADR 011 flag.** "Prior behaviour" here is an entire provider *implementation*: a flag would mean keeping `langchain-anthropic`, both construction paths and a doubled Ollama test matrix, to preserve a path measured to fail 46% of diary calls. ADR 011 §3 exempts non-gameplay changes, and **the A/B mechanism for a provider swap is the ledger, not an env flag** — records already carry `code.commit`, model digests and `server_version`, and gain `provider.structured_output`. The parity that does need testing is *cloud* parity, which is a construction-boundary assertion, not a flag-off test.

### Risks

| risk | mitigation |
|---|---|
| **The acceptance criterion asserts a zero the mechanism cannot guarantee.** Per §1(d), `DayAction`, `Roster` and `Pointing` substitute on **content**; this run's own `self_vote.initiation` was 2/29, and a self-targeted vote that repeats on retry substitutes. | The author's ratified decision is to keep "each reads zero" as written. Recorded here: under **CR 005** a nonzero kind **refutes** the hypothesis rather than failing the spec, provided the refutation is diagnosed in the record's notes. Negative-control tests (§4) exist so nobody reads ADR 013's "unrepresentable" language as a reason not to look. |
| **The thought-channel reshape is the largest blast radius in this spec** — reducer, four `_private_record_block` call sites, the `thoughts_before` cursor's readers, the transcript writer, and two specs' test suites. | A backward-compatible read helper (accepts `str` or `ThoughtRecord`) keeps it incremental rather than big-bang. No checkpoint crosses sessions, so there is no migration. Size it as its own slice. |
| **`method="json_schema"` is `ChatOllama`'s default, so the seam's value is defensive.** A reviewer may reasonably ask why it exists. | Stated as such in §1 rather than dressed as necessity: the seam pins the property against a default flip and prevents the method leaking to Bedrock, and the wire-level `format` assertion is what proves grammar-constrained decoding rather than mere construction. |
| **Two mechanisms now know about substitution** — the carried marker and `score_diary_fallback`'s equality. They can disagree. | Deliberate: the equality scorer is retained *as* the cross-check, with a test asserting the two agree on one driven game. Disagreement means a path forgot to signal, which is the failure worth catching. |
| **The suite is structurally blind to the swap.** `safe_llm` patches six *call-site* bindings, never `graphia.llm`, so no gameplay-path test reaches `_resolve_provider()`. | §4 Layer 3 re-points those bindings back at the real factories for one test module. Plus a provider-level backstop in `safe_llm` so a *new* module importing `get_large` from `graphia.llm` is netted rather than unnetted. |
| **`test_dual_mode_smoke.py` passes while measuring the fallback** — both runs script no diaries, so the queue raises and `_ai_diary` swallows it; **every diary entry in both runs is the stand-in today.** | Per the author's rule (script diaries only where they verify diaries or influence outcomes), that test's compared surface is `winner` / `kill_log` / the public transcript, and `day_diary` emits no `messages` key — so diaries do **not** influence its outcome and it is **not** rescripted. Instead: `FakeLargeUnified` serves a recognisable **default** for a known-schema empty queue rather than raising, so the substitution path is never silently exercised, and the test's docstring records that it is not diaries evidence. An **unknown** schema still fails loudly. |
| A langchain default flip makes `ChatOllama` validate on init and the offline suite starts hitting HTTP. | Pin `validate_model_on_init is False` in the construction test, so it surfaces as a red assertion rather than a hanging suite. |

---

## 4. Testing Strategy

**Three layers, because the construction boundary alone cannot cover this.**

**Layer 1 — construction boundary** (`test_llm_provider_construction.py`, rewritten). Assert `isinstance(client.inner, ChatOllama)`, and that config *flows into* `model` / `base_url` / `temperature` / `num_ctx`. Delete every `anthropic_api_url` / dummy-key / `max_tokens == 1024` assertion. Add the `validate_model_on_init` pin. Do **not** assert `keep_alive`, `client_kwargs`, or step class names — that is a mirror of the implementation.

**Layer 2 — the seam** (`test_structured_output_method.py`, new). A recording stub captures `(schema, kwargs)`. Parametrized over all seven schemas: the Ollama wrapper requests `method="json_schema"`; **the Bedrock wrappers request no method at all** — the only offline expression of "cloud games behave as before". Caller kwargs win and are never dropped. The wrapper is transparent to `InstrumentedModel` in **both** nesting orders, including the arrangement `ollama_smoke` actually builds — a proxy eating the method kwarg would disable grammar-constrained decoding *only under the eval harness*, which is the worst failure mode this project has. Then the assertion that carries the real weight: `runnable.first.kwargs["format"] == Schema.model_json_schema()` for each of the seven, with `method="function_calling"` as the **negative control** (`tools` present, `format` absent, different parser) so the positive assertion is not vacuous.

**Layer 3 — through the node, past `safe_llm`** (`test_provider_seam_through_the_node.py`, new). A fixture installs a fake `LLMProvider` whose tiers return the **real production wrapper** around a recording chat model, nulls the module cache, and re-points the six call-site bindings back at the real `graphia.llm` factories — undoing `safe_llm` for this module only, via the same `monkeypatch` surface. Parametrized per call site: each requests the provider's method; and the Bedrock twin requests none. Plus `test_the_recorded_schema_set_is_exactly_GAMEPLAY_SCHEMAS`, so an eighth call site added without coverage fails a red test instead of none.

**Closing the silent-failure trap** (`test_conftest_guards.py`, new). Three complementary parts, because a strict signature alone leaves the deeper half: (1) every fake takes `**kwargs` and **records** `(schema, kwargs)` rather than rejecting — the fake stands in for a model that accepts kwargs, so a strict signature makes it *less* faithful; (2) the genuinely unserviceable case (**unknown** schema) uses `pytest.fail(..., pytrace=False)`, whose `Failed` derives from `BaseException` and therefore **cannot** be caught by `_ai_diary`'s `except Exception` — retiring a hazard documented and tolerated for two specs; (3) an autouse teardown tripwire collecting violations, which survives *any* swallow in *any* thread. The headline test is parametrized over all seven fake classes: `with_structured_output(Pointing, method="json_schema")` neither raises nor records a violation. **That one test is what the suite lacks and what would have caught the typed signature the moment `method=` appeared.**

**The instrumentation regression** (extend `test_instrument_capture.py`): an `include_raw` mapping with a good `parsed` books zero failures — the test that would have caught it; a mapping with `parsed: None` books one, and `last_error` must not read `"dict"`; a bare non-instance result is **still** a failure (so the fix cannot over-reach into "any dict passes", the same bug wearing the opposite sign).

**The new behaviours.** Substitution counting, `test_substitution_counts.py`: a seven-way sweep asserting that a forced failure at each call site records **exactly one** substitution *and* produces the known fallback (either assertion alone is vacuous), paired with the anti-vacuity twin — a scripted success records **none** and returns the scripted value. Two players failing books two, so the counter counts items not fan-outs. Plus the negative controls §3 demands: `test_a_schema_valid_but_model_invalid_day_action_still_reaches_the_substitution_path` and its `Roster` twin. Marking, `test_spec041_substitution_marking.py`: the exact attribute literal; **an authored entry renders byte-identically to today** (an unconditional flag fails here); writer and reader agree on the literal; the flag splits no span; both directions of the body kind; absent-means-authored; and defensive non-bool values are ignored rather than guessed. While there: `test_one_line_sections_are_written_inline` currently sweeps only `<kill>` and `<recap>` — **there is no single-line assertion for `<diary>` or `<thought>` today**, and single-line-ness rests entirely on `_clamp_diary_entry` upstream. Extend it.

**Anti-vacuity, restated for this spec.** `_DIARY_FALLBACK` is itself non-empty in-voice prose, so "an entry exists" proves nothing. Every positive assertion pins the scripted value by identity, asserts it is **not** the sentinel (importing the constant, never copying it), and asserts the fake's queue was actually reached — the pattern `test_slice39_diary_fake_coverage.py` already established.

**Withdrawal hygiene** (`test_spec041_withdrawal.py`, new). Parametrized `not hasattr(day, name)` over the four withdrawn symbols; `not hasattr(day, "json")` (a module-scope import creates exactly that attribute); a recording fake asserting the diary call is now `(Diary, {})` — the behavioural inverse of the deleted `include_raw` assertion, and the only thing that catches "the withdrawal edited the docstring but left the kwarg"; and a source sweep over `src/` and `tests/` for the withdrawn names plus `langchain_anthropic` / `ChatAnthropic` / the dummy key, and `langchain-anthropic` absent from `pyproject.toml`. **This is what turns the functional spec's "gone rather than left passing against nothing" into a checked criterion instead of a review note.** Exclude `context/` — the ADRs must keep naming what they retired.

**Offline versus live, stated plainly.** Offline with fakes: every substitution **count**, the marking in both directions, the ledger key shape, the absent-not-zero gate, the counter-versus-scorer corroboration, and the seam requesting `json_schema`. **Live only:** whether the rate is actually zero on a real model — no fake can establish that. The gate is `make ollama-smoke` (extended per §2.7), kept **out of pytest**: a live test there would have to override the autouse `safe_llm`, and an overridable safety net is a weaker net for the other 1829 tests. Its pure decision logic (`_judge`, `_parse_pairs`, `_json_payload`) gets offline tests — `test_ollama_smoke_gate.py`, where **none exist today** — including the regression test for the zero-attempt hole.

**A manual step for the task list, not a committed test:** after the rewrite, deliberately make the wrapper request `method="function_calling"` and confirm the new diary tests go **red**. A suite that stays green under a broken transport is the exact failure this spec exists to fix.
