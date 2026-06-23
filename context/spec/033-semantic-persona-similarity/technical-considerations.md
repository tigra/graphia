<!--
Technical considerations for spec 033 — Semantic (Meaning-Based) Persona Similarity.
HOW the Bedrock-embedding cosine metric + its mocking + backfill are built. Builds on spec 032.
-->

# Technical Specification: Semantic (Meaning-Based) Persona Similarity

- **Functional Specification:** `./functional-spec.md`
- **Status:** Draft
- **Author(s):** Alexey Tigarev

> **Builds on spec 032.** 032 introduces the **value-type metric shape** (`{value, denominator}`, no rate/CI), the **value-type viewer rendering**, and the **transcript backfill harness**. 033 reuses all three and adds only the semantic scorer + its model wiring. Implement after 032.

> **Architecture-decision flag:** this introduces a **model-dependent metric** (a cloud embedding model the metric pipeline did not previously need) — a deliberate exception to architecture §6's "metrics are pure/lexical/local". That decision (which embedding model; the Bedrock-on-every-run dependency; the §6 exception) is **ADR-worthy**; recommend recording an ADR alongside this spec.

---

## 1. High-Level Technical Approach

Spec 031/032 measure persona similarity **lexically** (`difflib` over the persona text). 033 adds a **semantic** measure: embed each AI persona's table-facing description into a vector with a **Bedrock embedding model** (Amazon Titan Text Embeddings — the AWS-native, same-stack-as-Nova choice; exact model id verified at implementation), take the **cosine similarity** of each unordered persona pair, and record the **mean** cosine per run as a new value-type metric `persona_sem_sim {mean, denominator}` beside the lexical ones. Embeddings are **deterministic** (same text + model → same vector), so the metric is **reproducible** — but it **depends on a cloud model on every run** (including ollama gameplay runs), which the offline test suite must **mock**. Past transcript-preserved runs are backfilled with the same harness 032 uses, here paying real embedding calls.

Affected files: `src/graphia/llm.py` (a new `get_embeddings()` factory), `src/graphia/tools/blunder_eval.py` (semantic scorer + `run_eval` aggregation), `src/graphia/eval_ledger.py` (`METRIC_ORDER` entry; **reuses** 032's value-type render), `tests/conftest.py` (mock the embeddings boundary), tests, and the backfill harness. **Unchanged:** the lexical metrics (031/032), game rules, the `Persona` schema, `METRICS_VERSION`.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### Component A — embedding model factory (`llm.py`)

- Add `get_embeddings() -> Embeddings` returning `langchain_aws.BedrockEmbeddings(model_id=<titan-text-embeddings-v2>, region_name=load_config().aws_region)`, mirroring the existing `get_large`/`get_small` `ChatBedrockConverse` factories.
- **It is always Bedrock**, independent of `GRAPHIA_LLM_PROVIDER` — the metric's *instrument* is fixed so the number is comparable across ollama and bedrock gameplay runs (a consistent measuring stick, not confounded by the gameplay model). This is the deliberate cross-provider dependency (see Risks).
- **Verify at implementation** (per the project's check-don't-guess rule): the exact Titan embeddings model id, its availability in the configured region, the `langchain_aws.BedrockEmbeddings` import path and `embed_documents` batch API, and the vector dimensionality. Do not hard-assert these in code without confirming against the live SDK/Bedrock.

### Component B — semantic scorer (`tools/blunder_eval.py`)

- **New pure-ish fn `score_persona_semantic_sim(players, embed_fn) -> {"mean": float | None, "denominator": int}`** — `embed_fn` is **injected** (so tests pass a deterministic fake; production passes `get_embeddings().embed_documents`).
  - Build each AI persona's table-facing text the **same** way as the lexical scorers (`personality + " " + manner + " " + public_persona`, never `true_self`; `_spec009_mask_names` against AI names so a shared name token doesn't drive cosine — confirm this masking choice is desirable for embeddings at implementation).
  - `embed_fn(texts)` → one vector per persona (**single batch call per game**). Cosine of each unordered pair; `mean` = average cosine over `C(n,2)` pairs; `denominator = C(n,2)`. `<2` AI personas → `{None, 0}`.
- Cosine helper: a tiny pure function (no heavy numeric dep required for ≤7 short vectors; confirm whether `numpy` is already available before adding any dependency).

### Component C — aggregation (`run_eval`)

- Per game, call `score_persona_semantic_sim(cap.players, embed_fn)` and accumulate the **sum of cosines** + pair count across games; after the loop, when pairs > 0, record `result.metrics["persona_sem_sim"] = {"mean": cos_sum_total / pairs_total, "denominator": pairs_total}` (a value-type facet, no CI — reuses 032's `_attach_ci` skip-when-no-`count`).
- **Graceful degradation:** if `get_embeddings()` is unavailable (e.g. an ollama run with no AWS credentials), the metric is **omitted** (the block is simply absent, rendering blank) and the eval continues — it must never crash a gameplay run. Log the omission.

### Component D — ledger record + viewer (`eval_ledger.py`)

- Append `("persona_sem_sim", "persona sem~")` to `METRIC_ORDER` (after the 032 lexical entries). **No new rendering code** — it reuses 032's value-type `~{mean:.2f} (n=…)` branch.

### Component E — test mocking (`tests/conftest.py`)

- Extend the autouse `safe_llm` fixture to also patch the embeddings call site (`get_embeddings`, wherever `blunder_eval` reaches it) with a **deterministic fake embedder** — e.g. a stable hash/bag-of-words → fixed-length vector — so the suite never reaches real Bedrock and tests are reproducible. A per-test fixture can install a fake that returns chosen vectors to drive specific cosine outcomes.

### Component F — backfill harness (one-off, additive)

- Reuse 032's transcript parser + additive text-surgery, but the scoring step now calls **real Bedrock embeddings** over each transcript-preserved run's reconstructed personas (a bounded cost: ~personas-per-run embed calls × the transcript-preserved runs). Additive-only edit of those records; never touches non-eligible records.

---

## 3. Impact and Risk Analysis

- **System dependencies — NEW: a Bedrock embedding model on every measured run.** This is the load-bearing consequence. Even otherwise-free local (ollama) runs now require AWS credentials + incur (small) embedding cost to produce this metric. *Mitigation:* graceful omit when embeddings are unavailable (Component C); the metric is optional, never blocking.
- **Architecture §6 exception (ADR-worthy).** Metrics were pure/lexical/local; this one depends on a cloud model. It is **reproducible** (embeddings are deterministic), which softens the §6 concern, but the dependency itself is a real posture change — record an ADR (model choice, cross-provider dependency, the exception).
- **Cost.** Embedding calls are far cheaper than generation, but they apply to every run + the backfill. Bounded and small; note it.
- **Validity is not assumed (effort-not-results, CR 005).** Out-of-suite: run a measured batch, compute `persona_sem_sim` and the lexical `persona_mean_sim` on the same runs, and record whether the semantic measure reads **higher on archetype-samey casts** than the lexical one (confirmed or refuted — both complete).
- **Name-masking before embedding** is a judgment call (avoids name-driven similarity but may slightly alter meaning); confirm at implementation.
- **`METRICS_VERSION`** unchanged (additive).

---

## 4. Testing Strategy

All-mocked — the embeddings boundary is faked (Component E); never reaches Bedrock.

- **Pure scorer** with an injected fake embedder: two personas given **identical vectors** → mean cosine ≈ 1.0; orthogonal vectors → ≈ 0; a known mix → expected mean; `<2` AI personas → `{None, 0}`; human excluded; `true_self` never embedded.
- **Aggregation:** mean cosine = Σ cosines / total pairs across games; omitted when no pairs.
- **Graceful degradation:** when the (faked) embedder raises / is unavailable, the metric is omitted and the run completes.
- **Record + viewer:** mocked `run_eval` writes `persona_sem_sim {mean, denominator}`; reuses 032's value-type render (`~mean (n=…)`); absent → blank; no CI; `METRICS_VERSION` unchanged. Reuse the ledger/transcript redirect.
- **`safe_llm` coverage:** a flag-on full eval test never reaches real Bedrock embeddings (the fake is installed at the boundary).
- **Regression:** full `uv run pytest -q` green incl. spec-031/032 persona tests, the viewer tests, and `tests/test_dual_mode_smoke.py`.
- **Out-of-suite (effort-not-results):** the semantic-vs-lexical comparison run described in §3.
