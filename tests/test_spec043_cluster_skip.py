"""The near-duplicate clustering skip is output-identical (spec 043, Slice 7, Task 7.2).

WHY THIS MODULE EXISTS AT ALL. Task 7.1 measured what spec 043's per-game
re-scoring costs and found minutes, not the seconds technical-considerations §2.8
(Cost of re-scoring, and the one number to measure) predicted: record 52 paid
``finalise_s=1189.729`` of ``run_s=2969.058`` — 40.07% observed, ≈17.1% intrinsic
once the run's memory-pressure degradation is factored out. Task 7.2 had to choose
between §2.8's named fallback (re-score on a cadence, at the price of a record
that is stale for up to *k*−1 games) and a cheaper *exact* pass. It chose the
latter: ``repetition_experiment._clusters`` now skips any pair whose ``difflib``
upper bound is already below the threshold, which took record 47's 902-line pool
from 92.7 s to 3.9 s.

AND WHY THE TESTS ARE THE DELIVERABLE, NOT AN AFTERTHOUGHT. That function is
shared. It backs ``blunder_eval.score_repetition`` (imported there as
``_spec009_clusters``) and so produces ``metrics.repetition`` — this project's
central AI-quality number — across every record in ``evals/blunder-ledger.yaml``;
it also backs ``repetition_experiment``'s own ``_near_dup_rate`` /
``_max_cluster``, i.e. the spec-009 A/B's decision metric. Spec 043's §3 Scope and
Boundaries puts *"Changing what is measured, or how any score is calculated"* out
of scope, so the *only* thing that licenses this change is that the output is
provably identical. "Provably" has to mean tests, not an argument in a docstring.

THE THREE GATES, AND WHAT EACH ONE CATCHES.

* **Section 2 — the oracle.** ``_clusters_before_the_skip`` below is the
  pre-optimisation implementation copied verbatim. Generated corpora are
  clustered both ways and the **partitions** are compared, not merely the
  near-duplicate counts — two different partitions can share a count.
* **Section 3 — the corpora.** Twelve generators, each seeded, each aimed at a
  way the skip or ``difflib``'s autojunk heuristic could misbehave: identical
  texts, one-character edits, pairs straddling the threshold from both sides,
  empty and mixed-empty pools, texts over and under autojunk's 200-character
  activation length, heavily repeated characters (autojunk's other trigger),
  wildly unequal lengths (where ``real_quick_ratio``'s length bound bites),
  non-ASCII, and speech-shaped text like the real thing.
* **Section 4 — the real pools, with the ledger as the oracle.** Every committed
  ``metrics.repetition.count`` was produced by the **pre-skip** implementation on
  that run's real pooled lines. So rebuilding a run's pool from its committed
  transcripts and reproducing its committed count *is* an equivalence check
  against the old code — without having to run the old code. Twenty of the 36
  ledger-linked transcript dirs rebuild to exactly their record's
  ``repetition.denominator``, and all twenty reproduce both ``count`` and ``rate``
  exactly; a curated seven of them run in the default suite and the full sweep is
  behind an env gate (see ``_FULL_SWEEP``).

THE TRAP THIS MODULE IS MOSTLY HERE TO CATCH. ``SequenceMatcher.ratio()`` is
**asymmetric**, so ``ratio(a, b)`` can differ from ``ratio(b, a)``. Two causes,
and the one everybody knows about is the smaller: autojunk keys off the *second*
sequence above 200 elements, **and** ``get_matching_blocks``'s recursive
longest-match decomposition is not symmetric at any length at all (82% of random
20-to-90-character pairs disagree between the two orientations). The pre-skip
code paired ``seq1 = texts[i]``, ``seq2 = texts[j]`` with ``i < j``. Reusing the
``b2j`` index the obvious way
(``set_seq2(texts[i])``, ``i`` outer) silently swaps every pair and moves record
47's count from 662 to **664** and record 52's from 574 to **572** — a metric
change hiding inside a pure speedup, invisible to anything but an output-equality
assertion. ``_clusters_seq2_is_the_smaller_index`` below is that wrong
implementation, kept as an **anti-vacuity control**: section 5 asserts it
genuinely disagrees with the oracle on these corpora, which is what proves the
equality tests above would go red on the orientation mutation rather than merely
happening to pass.

NO MODEL, NO NETWORK, NO LEDGER WRITE. Everything here is pure functions over
strings plus read-only reads of ``evals/blunder-ledger.yaml`` and
``evals/transcripts/``. Nothing is written anywhere.

WHAT IS DELIBERATELY NOT HERE. The *cost* measurement is Task 7.1's and lives in
``tests/test_finalise_cost.py`` (the instrument) and §2.8 (the figures). This
module says nothing about how fast the new code is — only that it computes the
same thing. A speedup that were somehow reverted would leave every test here
green, and that is correct: the scores are the contract, the seconds are not.
"""

from __future__ import annotations

import difflib
import os
import random
import re
from pathlib import Path

import pytest
import yaml

from graphia.tools.blunder_eval import score_repetition
from graphia.tools.repetition_experiment import (
    _clusters,
    _mask_names,
    _max_cluster,
    _near_dup_rate,
    _normalize,
)

# The threshold ``score_repetition`` actually uses, plus the two neighbours
# ``repetition_experiment._metrics`` reports (``near80`` / ``near90``). Testing all
# three keeps the skip honest at the ends of the range rather than only where the
# production metric happens to sit.
_THRESHOLDS = (0.80, 0.85, 0.90)

_PRODUCTION_THRESHOLD = 0.85


# ==========================================================================
# 1. Two reference implementations: the old one, and the wrong one
# ==========================================================================


def _clusters_before_the_skip(texts: list[str], threshold: float) -> list[list[int]]:
    """``repetition_experiment._clusters`` as it stood before Task 7.2, **verbatim**.

    A copy, character for character, of the body this module exists to prove the
    new implementation equal to. It is **a test oracle and nothing else** — it is
    not imported by any production code, it is not a fallback, and it must never
    be "kept in sync" with the real function: the whole point is that it does not
    change when the real one does, so a behavioural drift shows up as a failure
    here instead of passing unnoticed.

    Naive all-pairs union-find with no early termination: C(n, 2) calls to
    ``SequenceMatcher.ratio()``, ``seq1`` the smaller index and ``seq2`` the
    larger. 406,351 calls on record 47's 902-line pool, which is what made spec
    043's per-game re-scoring cost minutes.
    """
    n = len(texts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if difflib.SequenceMatcher(None, texts[i], texts[j]).ratio() >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _clusters_seq2_is_the_smaller_index(
    texts: list[str], threshold: float
) -> list[list[int]]:
    """The skip written the *wrong* way round — the mutation this module must catch.

    Identical to the shipped implementation except that the reused ``b2j`` index is
    built on the **smaller**-index text (``set_seq2(texts[i])``, ``i`` outer), so
    every pair is measured in the opposite orientation from the pre-skip code.
    Because ``SequenceMatcher.ratio()`` is asymmetric — under autojunk above 200
    elements, and through ``get_matching_blocks`` at any length — that changes the
    metric: 662 → 664 on record 47's pool, 574 → 572 on record 52's.

    Kept here as the **anti-vacuity control** for the corpora in section 3. A
    corpus on which this variant agrees with the oracle cannot catch an
    orientation swap, so section 5 asserts that it disagrees — which is what makes
    the equality tests above meaningful rather than lucky.
    """
    n = len(texts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    matcher = difflib.SequenceMatcher(None)
    for i in range(n):
        matcher.set_seq2(texts[i])
        for j in range(i + 1, n):
            matcher.set_seq1(texts[j])
            if matcher.real_quick_ratio() < threshold:
                continue
            if matcher.quick_ratio() < threshold:
                continue
            if matcher.ratio() >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _near_dup_count(groups: list[list[int]]) -> int:
    """``score_repetition``'s numerator: members of clusters of size > 1."""
    return sum(len(c) for c in groups if len(c) > 1)


# ==========================================================================
# 2. The corpora: twelve generators, each aimed at one way this could break
# ==========================================================================
#
# Each generator takes a seeded ``random.Random`` and returns a list of texts.
# Sizes are kept small (12–18 texts) because the *oracle* is quadratic and runs
# in the default suite: 18 texts is 153 pairs, and the whole of section 3 costs
# under two seconds. What matters is the shape of the input, not its volume — the
# volume case is section 4's real 902-line pool.

_WORDS = (
    "suspicious quiet vote accuse defend citizen mafioso night day "
    "watching trust nothing settled closely whoever misdirect us"
).split()


def _sentence(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(words))


def _gen_identical(rng: random.Random) -> list[str]:
    """Every text the same. Ratio 1.0 for every pair; no bound may skip any of them."""
    return [_sentence(rng, 12)] * 14


def _gen_one_char_apart(rng: random.Random) -> list[str]:
    """A base text with single-character edits — ratios just under 1.0.

    The case where both bounds pass comfortably and the real ``ratio()`` decides.
    """
    base = _sentence(rng, 20)
    out = [base]
    for _ in range(13):
        pos = rng.randrange(len(base))
        out.append(base[:pos] + rng.choice("qzxjk") + base[pos + 1 :])
    return out


def _gen_threshold_straddle(rng: random.Random) -> list[str]:
    """Pairs engineered to land just either side of the thresholds.

    A shared prefix of controlled length plus divergent tails, so the pairwise
    ratios sweep roughly 0.70–0.98 and every threshold in ``_THRESHOLDS`` has
    pairs immediately above and below it. This is where an off-by-one in a
    ``<`` / ``<=`` on a bound would show.
    """
    base = _sentence(rng, 30)
    out = [base]
    for keep in (0.72, 0.78, 0.82, 0.84, 0.86, 0.88, 0.92, 0.96):
        cut = int(len(base) * keep)
        out.append(base[:cut] + _sentence(rng, 4))
    for keep in (0.75, 0.85, 0.95):
        cut = int(len(base) * keep)
        out.append(base[:cut])
    return out


def _gen_all_empty(rng: random.Random) -> list[str]:
    """Nothing but empty strings — the degenerate pool ``score_repetition`` can be handed.

    ``difflib`` reports 1.0 for two empty sequences (zero-length total), so the
    oracle unions everything; the skip must too. Also exercises the ``T == 0``
    branch of ``_calculate_ratio`` in all three of the bound functions.
    """
    return [""] * 12


def _gen_empty_mixed(rng: random.Random) -> list[str]:
    """Empty strings beside real ones — ``real_quick_ratio`` is 0.0 for those pairs.

    The pair (empty, non-empty) is the extreme of the length bound: it must be
    skipped, and the empties must still cluster with each other.
    """
    out: list[str] = []
    for i in range(14):
        out.append("" if i % 3 == 0 else _sentence(rng, rng.randint(3, 15)))
    return out


def _gen_long_autojunk_active(rng: random.Random) -> list[str]:
    """Texts over 200 characters — autojunk's activation length.

    ``SequenceMatcher`` only marks popular elements as junk when ``len(b) >= 200``,
    and it does so from **seq2** alone. Above that line the orientation gains a
    second, much coarser way to matter — a popular element junked on one side and
    not the other can move ``ratio()`` by whole percentage points, which is the
    mechanism behind record 47's 662-vs-664.
    """
    base = "".join(rng.choice("abcde ") for _ in range(240))
    out = [base]
    for _ in range(11):
        tail = rng.choice("xyz") * rng.randint(4, 40)
        cut = rng.randint(150, 240)
        out.append(base[:cut] + tail)
    return out


def _gen_short_autojunk_inactive(rng: random.Random) -> list[str]:
    """Texts well under 200 characters — autojunk never fires.

    The control for the generator above, and the one that corrected a wrong belief
    while this module was being written. The carry-forward note that proposed the
    skip attributed ``ratio()``'s asymmetry to autojunk alone, which would mean
    orientation could only matter above 200 characters. It does not: this corpus
    catches the orientation mutation too (at threshold 0.90), because
    ``get_matching_blocks``'s recursive longest-match decomposition is not
    symmetric either — 82% of random 20-to-90-character pairs measure differently
    in the two orientations. The trap is wider than it was described as, which is
    a reason to keep this generator rather than treat it as the boring control.
    """
    base = "".join(rng.choice("abcde ") for _ in range(90))
    out = [base]
    for _ in range(13):
        cut = rng.randint(40, 90)
        out.append(base[:cut] + "".join(rng.choice("abcde ") for _ in range(8)))
    return out


def _gen_repeated_characters(rng: random.Random) -> list[str]:
    """A two-character alphabet, heavily repeated — autojunk's *popularity* trigger.

    Every element occurs in far more than 1% of a 200+ character sequence, so
    ``SequenceMatcher`` marks essentially everything junk in seq2 and ``ratio()``
    collapses while ``quick_ratio()`` (which ignores junk entirely) stays high.
    That is the widest gap between bound and truth this module can manufacture,
    and therefore the case where a skip that trusted the bound too far would be
    caught.
    """
    out: list[str] = []
    for _ in range(12):
        out.append("".join(rng.choice("ab") for _ in range(rng.randint(180, 320))))
    return out


def _gen_unequal_lengths(rng: random.Random) -> list[str]:
    """Lengths spanning 1 to ~500 — where ``real_quick_ratio``'s length bound does the work.

    ``real_quick_ratio`` is ``2 * min(la, lb) / (la + lb)``, so a 10-character text
    beside a 400-character one is bounded at 0.049 and skipped without ``difflib``
    looking at a single character. Most of the real 902-line pool's 97.6% skip rate
    comes from exactly this.
    """
    out: list[str] = []
    for length in (1, 2, 5, 10, 25, 60, 120, 200, 260, 340, 420, 500):
        out.append("".join(rng.choice("abcdef ") for _ in range(length)))
    return out


def _gen_non_ascii(rng: random.Random) -> list[str]:
    """Cyrillic, CJK, combining marks and emoji — ``difflib`` compares code points.

    Graphia's rosters are deliberately multi-cultural and its models emit emoji, so
    non-ASCII pooled lines are ordinary rather than exotic. Included because a
    bound that reasoned about bytes rather than code points would differ here.
    """
    alphabets = ("абвгдежз", "日本語のテスト", "éàçüñö", "🙂🎭🔪🌙", "क्षत्रिय")
    out: list[str] = []
    for _ in range(14):
        alpha = rng.choice(alphabets)
        out.append("".join(rng.choice(alpha) for _ in range(rng.randint(8, 60))))
    return out


def _gen_speech_shaped(rng: random.Random) -> list[str]:
    """Sentences from a small vocabulary — the shape of real pooled Day speech.

    The realistic case: many texts sharing most of their vocabulary, a handful of
    genuine repeats, ratios clustered in the 0.4–0.9 band where the ``quick_ratio``
    bound (character multisets, blind to order) is at its least discriminating.
    """
    out: list[str] = []
    for _ in range(16):
        out.append(_sentence(rng, rng.randint(8, 30)))
    # A couple of exact repeats and a couple of near-repeats, as a real pool has.
    out.append(out[0])
    out.append(out[1] + " indeed")
    return out


def _gen_singleton_and_pair(rng: random.Random) -> list[str]:
    """Two texts, and one text — the smallest inputs with any pair at all.

    ``n == 0`` and ``n == 1`` have no pairs and cannot distinguish the
    implementations; ``n == 2`` is the smallest input that can, and the shipped
    loop's ``for i in range(j)`` inner bound is where an off-by-one would strand
    it. Padded to four texts so there is also a pair that must *not* union.
    """
    a = _sentence(rng, 10)
    return [a, a, _sentence(rng, 10), ""]


_GENERATORS = {
    "identical": _gen_identical,
    "one-char-apart": _gen_one_char_apart,
    "threshold-straddle": _gen_threshold_straddle,
    "all-empty": _gen_all_empty,
    "empty-mixed": _gen_empty_mixed,
    "long-autojunk-active": _gen_long_autojunk_active,
    "short-autojunk-inactive": _gen_short_autojunk_inactive,
    "repeated-characters": _gen_repeated_characters,
    "unequal-lengths": _gen_unequal_lengths,
    "non-ascii": _gen_non_ascii,
    "speech-shaped": _gen_speech_shaped,
    "singleton-and-pair": _gen_singleton_and_pair,
}

# Fixed seeds: the corpora are randomised for coverage but the *suite* is not — a
# failure here is reproducible from the test id alone, and a green run means the
# same inputs stayed green rather than a fresh draw happening to avoid the bug.
_SEEDS = (20260914, 7, 991)

_CORPUS_PARAMS = [
    pytest.param(name, seed, id=f"{name}-seed{seed}")
    for name in _GENERATORS
    for seed in _SEEDS
]


def _corpus(name: str, seed: int) -> list[str]:
    return _GENERATORS[name](random.Random(seed))


# ==========================================================================
# 3. The equality gate: same partition as the pre-skip implementation
# ==========================================================================


@pytest.mark.parametrize(("name", "seed"), _CORPUS_PARAMS)
def test_the_skip_returns_the_oracles_exact_partition(name: str, seed: int) -> None:
    """``_clusters`` == the verbatim pre-skip implementation, on every corpus.

    Asserted on the **whole return value**, which is the partition: one ascending
    index list per equivalence class, the classes ordered by their smallest member.
    That shape is a function of the partition alone (the groups are collected by
    walking ``i`` ascending), so list equality here *is* partition equality — and
    it is strictly stronger than comparing near-duplicate counts, which two
    different partitions can share.

    Run at all three thresholds the project reports, not just the 0.85 that
    ``score_repetition`` uses, so ``repetition_experiment``'s own ``near80`` /
    ``near90`` are covered by the same proof.
    """
    texts = _corpus(name, seed)
    for threshold in _THRESHOLDS:
        expected = _clusters_before_the_skip(texts, threshold)
        actual = _clusters(texts, threshold)
        assert actual == expected, f"{name}/seed{seed} @ {threshold}"
        # ``_max_cluster``'s output, asserted here because the oracle partition is
        # already in hand. It is the third production consumer of this function
        # (after ``score_repetition`` and ``_near_dup_rate``) and feeds
        # ``GameMetrics.max_cluster`` in the spec-009 A/B's per-game table.
        assert _max_cluster(texts, threshold) == max(
            (len(c) for c in expected), default=0
        ), f"{name}/seed{seed} @ {threshold}"


@pytest.mark.parametrize(("name", "seed"), _CORPUS_PARAMS)
def test_the_skip_returns_the_oracles_exact_near_dup_rate(
    name: str, seed: int
) -> None:
    """The metric on top of the partition is equal too — floats included.

    ``_near_dup_rate`` is what ``repetition_experiment``'s decision metric actually
    reports and what ``score_repetition``'s ``rate`` mirrors, so it is asserted
    directly rather than inferred from the partition. Exact float equality, not
    ``approx``: the rate is ``count / len(texts)`` over integers, and any
    difference at all would mean a different count.
    """
    texts = _corpus(name, seed)
    for threshold in _THRESHOLDS:
        assert _near_dup_rate(texts, threshold) == _near_dup_rate_via_oracle(
            texts, threshold
        ), f"{name}/seed{seed} @ {threshold}"


def _near_dup_rate_via_oracle(texts: list[str], threshold: float) -> float:
    """``_near_dup_rate``'s body over the oracle's clustering."""
    if not texts:
        return 0.0
    return _near_dup_count(_clusters_before_the_skip(texts, threshold)) / len(texts)


def test_no_pairs_at_all_is_a_pool_of_singletons() -> None:
    """The two inputs with no pairs: empty, and one text.

    Below the parametrized corpora because a generator cannot express them (a
    zero-length corpus makes every assertion vacuously true). The shipped loop's
    ``for j in range(n) / for i in range(j)`` must still return the partition, not
    an empty list, for ``n`` in 0 and 1 — ``_near_dup_rate`` divides by
    ``len(texts)`` and ``_max_cluster`` takes a ``max`` over the result.
    """
    assert _clusters([], _PRODUCTION_THRESHOLD) == []
    assert _clusters(["only one"], _PRODUCTION_THRESHOLD) == [[0]]
    assert _clusters([], _PRODUCTION_THRESHOLD) == _clusters_before_the_skip(
        [], _PRODUCTION_THRESHOLD
    )
    assert _clusters(["only one"], _PRODUCTION_THRESHOLD) == (
        _clusters_before_the_skip(["only one"], _PRODUCTION_THRESHOLD)
    )


# The boundary pair: 17 characters against the same 17 plus 6 more. All three of
# ``difflib``'s measures land on **exactly** 0.85 — ``ratio`` because 17 of the 40
# total characters match on each side (2 × 17 / 40), ``real_quick_ratio`` because
# the length bound is the same 2 × 17 / 40, and ``quick_ratio`` in between. Which
# makes this one pair the entire boundary contract of the skip: three ``<``
# comparisons that must not fire at equality, and one ``>=`` that must.
_AT_THRESHOLD_A = "abcdefghijklmnopq"
_AT_THRESHOLD_B = "abcdefghijklmnopqrstuvw"


def test_a_pair_sitting_exactly_on_the_threshold_still_clusters() -> None:
    """Equality is inclusive, in the skip's bounds and in the verdict alike.

    The pre-skip code asked ``ratio() >= threshold``, so a pair measuring exactly
    0.85 is a near-duplicate. The skip adds two more comparisons in front of it
    (``real_quick_ratio() < threshold``, ``quick_ratio() < threshold``), and each
    one is a place where ``<`` written as ``<=`` would throw that pair away —
    silently, because nothing else in the module manufactures a pair that lands on
    the boundary exactly.

    The three ``==`` assertions are not decoration: they are what keeps this test
    from going vacuous if a future ``difflib`` changes how any of the three is
    computed. If the pair stops sitting on the boundary, this fails loudly instead
    of continuing to "pass" about nothing.
    """
    matcher = difflib.SequenceMatcher(None, _AT_THRESHOLD_A, _AT_THRESHOLD_B)
    assert matcher.real_quick_ratio() == _PRODUCTION_THRESHOLD
    assert matcher.quick_ratio() == _PRODUCTION_THRESHOLD
    assert matcher.ratio() == _PRODUCTION_THRESHOLD

    texts = [_AT_THRESHOLD_A, _AT_THRESHOLD_B]
    assert _clusters(texts, _PRODUCTION_THRESHOLD) == [[0, 1]]
    assert _clusters(texts, _PRODUCTION_THRESHOLD) == _clusters_before_the_skip(
        texts, _PRODUCTION_THRESHOLD
    )


def test_a_pair_just_under_the_threshold_does_not_cluster() -> None:
    """The other side of the boundary, so the test above cannot pass by unioning everything.

    One character longer on the right: ``ratio`` drops to 2 × 17 / 41 ≈ 0.829 and
    the pair must stay in separate clusters. Without this, an implementation that
    unioned every pair unconditionally would satisfy the boundary test.
    """
    texts = [_AT_THRESHOLD_A, _AT_THRESHOLD_B + "z"]
    assert difflib.SequenceMatcher(None, *texts).ratio() < _PRODUCTION_THRESHOLD
    assert _clusters(texts, _PRODUCTION_THRESHOLD) == [[0], [1]]
    assert _clusters(texts, _PRODUCTION_THRESHOLD) == _clusters_before_the_skip(
        texts, _PRODUCTION_THRESHOLD
    )


# ==========================================================================
# 4. The real pools: the committed ledger is the oracle
# ==========================================================================
#
# Every ``metrics.repetition.count`` in ``evals/blunder-ledger.yaml`` was produced
# by the PRE-SKIP implementation on that run's real pooled AI lines. Rebuilding the
# pool from the run's committed transcripts and reproducing the committed count is
# therefore an equivalence check against the old code on real data, at a scale the
# generated corpora cannot reach — without running the old code at all.
#
# The reader's fidelity is a PRECONDITION, not an assumption: a rebuilt pool is
# only compared when its length equals the record's own ``repetition.denominator``.
# Where the reader cannot reproduce that number the pool is excluded rather than
# counted — 16 of the 36 ledger-linked dirs are excluded that way (records 15–17
# predate the spec-022 structured transcript format entirely; the rest drift by
# 1–11% as the format changed across a year of specs). That is a limitation of this
# reader, not evidence about the skip, and section 4's census asserts a floor on how
# many pools did qualify so the sweep cannot quietly go vacuous.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LEDGER = _REPO_ROOT / "evals" / "blunder-ledger.yaml"
_TRANSCRIPTS_ROOT = _REPO_ROOT / "evals" / "transcripts"

# The full 20-pool sweep costs ~22 s (and the oracle on record 47's 902-line pool
# a further ~93 s), so the default suite runs a curated seven and this gate opens
# the rest. Set it when touching ``_clusters`` or ``score_repetition``.
_FULL_SWEEP = os.environ.get("GRAPHIA_SPEC043_FULL_EQUIVALENCE") == "1"
_FULL_SWEEP_REASON = (
    "slow real-pool sweep — set GRAPHIA_SPEC043_FULL_EQUIVALENCE=1 to run "
    "(≈2 minutes: all 20 rebuildable pools, plus the pre-skip oracle on record "
    "47's 902-line pool)"
)

# The default-suite selection: three providers, 22 → 264 pooled lines, and both
# ends of the density range (record 40 is 80/108 near-duplicate, record 38 is
# 2/22). The three zero-count pools are the `bedrock-claude` arm's, and record 50
# is kept in because it is the one shape a *wrongly-excluding* skip could not be
# caught by while a *wrongly-including* one could.
_DEFAULT_POOL_DIRS = (
    "2026-09-04T13-13-43",  # record 38, ollama, 22 lines, count 2
    "2026-09-07T21-56-47",  # record 42, ollama, 43 lines, count 17
    "2026-09-04T17-10-11",  # record 39, ollama, 70 lines, count 50
    "2026-09-09T18-41-05",  # record 50, bedrock-claude, 72 lines, count 0
    "2026-09-05T17-58-24",  # record 40, ollama, 108 lines, count 80
    "2026-09-09T18-41-01",  # record 49, bedrock, 169 lines, count 96
    "2026-09-14T13-58-16-ca5bce",  # record 51, ollama, 264 lines — spec 043's own run
)

# Record 47: the pool §2.8's whole cost model is fitted to, and the one with
# partition-level (not merely count-level) identity from the direct comparison.
_BIG_POOL_DIR = "2026-09-10T13-31-38"

_PLAYER_TAG = re.compile(r'<player name="([^"]+)"(?P<attrs>[^>]*)>')
_SPEECH_LINE = re.compile(r"^([^:<][^:]*): (.*)$")


def _read_game_pool(path: Path) -> tuple[list[str], set[str]]:
    """Rebuild one game's AI Day-speech lines and AI names from its transcript.

    The same reconstruction technical-considerations §2.8 (Cost of re-scoring, and
    the one number to measure) describes and proves faithful: lines of the form
    ``Name: text`` inside a ``<day>`` block, excluding ``Moderator:``, excluding
    the ``human="true"`` seat, and **excluding the contents of ``<vote …>``
    blocks** — ballot answers ("Aiko: Yes") are not Day speech, and counting them
    over-states the pool by exactly ``substitutions.ballot.attempted``. Private
    thoughts, diaries and recaps are all tag-led, so ``_SPEECH_LINE``'s leading
    ``[^:<]`` drops them.

    This is deliberately a *test* reader. Production never reconstructs a pool
    from a transcript — ``run_eval`` accumulates it from live messages — so
    putting this in ``src/`` would be inventing a production surface to serve a
    test. Its fidelity is checked rather than trusted: see the denominator
    precondition in ``_rebuildable_pools``.
    """
    text = path.read_text(encoding="utf-8")
    setup = re.search(r"<setup>(.*?)</setup>", text, re.S)
    if setup is None:
        return [], set()
    ai_names = {
        match.group(1)
        for match in _PLAYER_TAG.finditer(setup.group(1))
        if 'human="true"' not in match.group("attrs")
    }
    lines: list[str] = []
    for day in re.findall(r"<day>(.*?)</day>", text, re.S):
        day = re.sub(r"<vote[^>]*>.*?</vote>", "", day, flags=re.S)
        for raw in day.splitlines():
            speech = _SPEECH_LINE.match(raw)
            if speech is not None and speech.group(1) in ai_names:
                lines.append(speech.group(2))
    return lines, ai_names


def _read_run_pool(run_dir: Path) -> tuple[list[str], set[str]]:
    """A run's whole pool: every game's lines in order, names as a growing union.

    Exactly how ``run_eval`` accumulates them — the pool is concatenated across
    games and ``ai_names`` is the union, which is why an old line's *masked* text
    changes as later games deal new names (§2.8, "There is no cheap caching fix").
    """
    pool: list[str] = []
    names: set[str] = set()
    for game in sorted(run_dir.glob("game-*.txt")):
        game_lines, game_names = _read_game_pool(game)
        pool.extend(game_lines)
        names |= game_names
    return pool, names


def _load_committed_records() -> list[dict]:
    if not _LEDGER.is_file():
        return []
    return [doc for doc in yaml.safe_load_all(_LEDGER.read_text()) if doc]


def _rebuildable_pools() -> list[tuple[str, dict]]:
    """``(transcript_dir, repetition)`` for records whose pool rebuilds exactly.

    The precondition: the rebuilt pool length must equal the record's committed
    ``repetition.denominator``. Only then is the committed ``count`` a statement
    about *this* list of lines, and only then does reproducing it prove anything.
    """
    out: list[tuple[str, dict]] = []
    for record in _load_committed_records():
        run = record.get("run") or {}
        metrics = record.get("metrics") or {}
        repetition = metrics.get("repetition")
        run_dir_name = run.get("transcript_dir")
        if not run_dir_name or not isinstance(repetition, dict):
            continue
        run_dir = _TRANSCRIPTS_ROOT / str(run_dir_name)
        if not run_dir.is_dir():
            continue
        lines, _ = _read_run_pool(run_dir)
        if len(lines) == repetition.get("denominator"):
            out.append((str(run_dir_name), repetition))
    return out


_requires_corpus = pytest.mark.skipif(
    not _LEDGER.is_file() or not _TRANSCRIPTS_ROOT.is_dir(),
    reason=(
        "no committed ledger/transcripts in this checkout — "
        "evals/transcripts/ is curated commit-or-delete and may be absent"
    ),
)


def _assert_reproduces_committed_repetition(run_dir_name: str) -> dict:
    """Rebuild the pool, score it with the shipped code, compare to the ledger."""
    run_dir = _TRANSCRIPTS_ROOT / run_dir_name
    assert run_dir.is_dir(), f"missing committed transcripts: {run_dir}"
    record = next(
        (
            r
            for r in _load_committed_records()
            if (r.get("run") or {}).get("transcript_dir") == run_dir_name
        ),
        None,
    )
    assert record is not None, f"no ledger record links {run_dir_name}"
    committed = (record.get("metrics") or {})["repetition"]

    lines, names = _read_run_pool(run_dir)
    # Fidelity first: without this the count comparison below is meaningless.
    assert len(lines) == committed["denominator"], (
        f"{run_dir_name}: rebuilt {len(lines)} lines against a committed "
        f"denominator of {committed['denominator']} — the reader, not the scorer"
    )
    scored = score_repetition(lines, names)
    assert scored["denominator"] == committed["denominator"], run_dir_name
    assert scored["count"] == committed["count"], run_dir_name
    assert scored["rate"] == committed["rate"], run_dir_name
    return scored


@_requires_corpus
@pytest.mark.parametrize("run_dir_name", _DEFAULT_POOL_DIRS)
def test_a_committed_records_repetition_is_reproduced_exactly(
    run_dir_name: str,
) -> None:
    """The shipped skip reproduces a pre-skip record's ``repetition``, field for field.

    ``rate`` is compared as an exact float, not ``approx``. It is
    ``count / denominator`` over two integers, so it is reproducible bit-for-bit if
    and only if the count is — and the ledger stores it to full precision
    (``0.7339246119733924``) precisely so that this kind of comparison is possible.
    """
    _assert_reproduces_committed_repetition(run_dir_name)


@_requires_corpus
def test_the_curated_selection_actually_spans_arms_and_shapes() -> None:
    """Anti-vacuity: the seven default pools are not seven copies of one case.

    A selection that had silently collapsed to (say) three zero-count Claude runs
    would still pass every test above while proving much less, and nothing else
    in this module would notice. So the properties the selection is chosen for are
    asserted rather than left in a comment: a range of pool sizes spanning more
    than an order of magnitude, at least one pool with **no** near-duplicates at
    all (the control a wrongly-*including* skip fails), and at least one where most
    of the pool is a near-duplicate (where the clustering does real transitive
    work).

    Reads the **committed** metrics rather than re-scoring — the re-scoring is the
    parametrized test above's job, and doing it twice costs two seconds to learn
    nothing new.
    """
    by_dir = {
        (record.get("run") or {}).get("transcript_dir"): (record.get("metrics") or {})
        for record in _load_committed_records()
    }
    pools = []
    for run_dir_name in _DEFAULT_POOL_DIRS:
        assert run_dir_name in by_dir, f"no ledger record links {run_dir_name}"
        pools.append(by_dir[run_dir_name]["repetition"])

    sizes = [p["denominator"] for p in pools]
    assert max(sizes) >= 10 * min(sizes), sizes
    assert any(p["count"] == 0 for p in pools), "no zero-count pool in the selection"
    assert any(
        p["count"] >= 0.7 * p["denominator"] for p in pools
    ), "no densely-duplicated pool in the selection"


@_requires_corpus
@pytest.mark.skipif(not _FULL_SWEEP, reason=_FULL_SWEEP_REASON)
def test_every_rebuildable_committed_pool_reproduces_its_record() -> None:
    """The whole sweep: every committed pool this reader can rebuild exactly.

    Twenty of the 36 ledger-linked transcript dirs qualify as of 2026-09-14, spanning all
    three providers (``ollama``, ``bedrock``, ``bedrock-claude``) and 22 → 902
    pooled lines, and every one of them reproduces its record's committed ``count``
    and ``rate``. The floor below is what stops a future reader regression from
    turning this into a green test over three pools; it is deliberately well under
    twenty so that adding a record or changing the transcript format does not fail
    it spuriously.
    """
    pools = _rebuildable_pools()
    assert len(pools) >= 15, f"only {len(pools)} pools rebuilt exactly"
    for run_dir_name, _ in pools:
        _assert_reproduces_committed_repetition(run_dir_name)


@_requires_corpus
@pytest.mark.skipif(not _FULL_SWEEP, reason=_FULL_SWEEP_REASON)
def test_the_902_line_pool_has_the_oracles_exact_partition() -> None:
    """Partition-level identity on the biggest real pool, against the old code itself.

    Record 47's 902-line pool is where §2.8's cost model was fitted and where the
    orientation trap was caught (662 against the wrong orientation's 664), so it is
    the one pool worth paying the pre-skip implementation's ~93 s to compare
    *partitions* on rather than counts. Everything else about this pool is covered
    by the count comparison in the sweep above, which is why this is the only test
    in the module that runs the oracle at scale.
    """
    lines, names = _read_run_pool(_TRANSCRIPTS_ROOT / _BIG_POOL_DIR)
    assert len(lines) == 902
    masked = [_normalize(_mask_names(line, names)) for line in lines]

    expected = _clusters_before_the_skip(masked, _PRODUCTION_THRESHOLD)
    actual = _clusters(masked, _PRODUCTION_THRESHOLD)

    assert actual == expected
    assert _near_dup_count(actual) == 662


# ==========================================================================
# 5. Anti-vacuity: the corpora would catch the mutations
# ==========================================================================


def _skip_census(texts: list[str], threshold: float) -> dict[str, int]:
    """How many pairs each bound rejects, computed independently of ``_clusters``.

    Recomputed here from ``difflib`` directly rather than instrumented into the
    production function, so it measures the *inputs* rather than the code under
    test. Used only to prove the corpora exercise the skip.
    """
    census = {"real_quick": 0, "quick": 0, "ratio": 0}
    for j in range(len(texts)):
        for i in range(j):
            matcher = difflib.SequenceMatcher(None, texts[i], texts[j])
            if matcher.real_quick_ratio() < threshold:
                census["real_quick"] += 1
            elif matcher.quick_ratio() < threshold:
                census["quick"] += 1
            else:
                census["ratio"] += 1
    return census


def test_both_upper_bounds_actually_reject_pairs_somewhere_in_the_corpora() -> None:
    """The skip is exercised, not merely present.

    If every pair in every corpus sailed past both bounds, section 3 would prove
    only that an unused branch is harmless. This asserts the opposite: across the
    corpora, ``real_quick_ratio`` (the length bound) rejects pairs, ``quick_ratio``
    (the character-multiset bound) rejects pairs the length bound let through, and
    pairs still reach the real ``ratio()``. All three arms of the shipped function
    are therefore taken.
    """
    totals = {"real_quick": 0, "quick": 0, "ratio": 0}
    for name in _GENERATORS:
        for seed in _SEEDS:
            for key, value in _skip_census(
                _corpus(name, seed), _PRODUCTION_THRESHOLD
            ).items():
                totals[key] += value

    assert totals["real_quick"] > 0, totals
    assert totals["quick"] > 0, totals
    assert totals["ratio"] > 0, totals


def test_the_corpora_would_catch_the_orientation_swap() -> None:
    """The control for the trap: the wrong orientation really does differ here.

    ``_clusters_seq2_is_the_smaller_index`` is the mutation this module was written
    to catch — the ``b2j``-reuse bug that moved record 47's count from 662 to 664.
    If it agreed with the oracle on these corpora, every equality assertion in
    section 3 would be passing for the wrong reason. So: at least one corpus must
    distinguish them, which is what makes "the orientation is pinned" a measured
    fact about this test module rather than a hope.

    Measured when the mutation was actually run: **eleven** tests in this module go
    red on it, including five of the seven real committed pools — records 38, 39,
    40, 49 and 51 all report a count their own ledger entry contradicts. Both the
    autojunk-active corpus *and* the short one catch it, which is the corrected
    version of the hazard: see ``_gen_short_autojunk_inactive``.
    """
    disagreeing = [
        f"{name}-seed{seed}"
        for name in _GENERATORS
        for seed in _SEEDS
        if _clusters_seq2_is_the_smaller_index(
            _corpus(name, seed), _PRODUCTION_THRESHOLD
        )
        != _clusters_before_the_skip(_corpus(name, seed), _PRODUCTION_THRESHOLD)
    ]
    assert disagreeing, (
        "no generated corpus distinguishes the two orientations — section 3's "
        "equality tests cannot catch the b2j-reuse trap"
    )


def test_the_oracle_is_not_the_shipped_function_by_accident() -> None:
    """The oracle is a copy, so prove it is a *different* implementation.

    A guard against the one way this whole module could become circular: if
    ``_clusters_before_the_skip`` were ever replaced by a re-export of the shipped
    function (a tidy-up, an import someone "simplified"), every equality test would
    pass trivially. Asserting the two are distinct objects is cheap and catches it.
    """
    assert _clusters_before_the_skip is not _clusters
    assert (
        _clusters_before_the_skip.__module__ == __name__
    ), "the oracle must live in this test module, not be imported from src/"
