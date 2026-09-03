"""Spec 042 (A Starter Table With Room For One Mistake) — Slice 2: the fixture
itself is under test.

WHAT THIS MODULE PINS
---------------------

Task 2.2 split ``fake_small``'s two call forms so they mean two different
things (tech-spec 042 §2.2):

* **The list form** — ``fake_small([...])`` — installs the PERMISSIVE
  :class:`conftest._PooledSmall`. No queue, no drain, no replay. Every
  ``.invoke`` asks the production helper
  :func:`graphia.nodes.setup.ai_name_count` how many names the *resolved
  config* wants and answers with exactly that many distinct names, treating the
  supplied list as a **pool** and extending it deterministically when the pool
  is short (:func:`conftest._pooled_names`). 82 scaffolding call sites use this
  form, and none of them should ever need resizing when the default table
  changes.
* **The ``outputs=`` form** — installs the STRICT :class:`conftest.FakeSmall`:
  a one-shot queue that raises ``AssertionError`` the moment it is asked for an
  answer nobody scripted.

WHY A TEST OF A TEST FIXTURE EARNS ITS PLACE
--------------------------------------------

Because all three of those properties are, as of Task 2.2, promises made only
in a docstring, and each one fails **silently** if it is broken:

1. **The count is derived at *invoke* time, deliberately.** A version that
   derived it at import, or in ``_PooledSmall.__init__``, would be pinned to
   whatever lineup was in force before the game started — and
   ``tests/test_configurable_lineup.py`` and its siblings vary the lineup *per
   test* through ``monkeypatch.setenv``. A single test at the default lineup
   cannot see the difference: an import-time derivation passes it. That is
   precisely why :func:`test_list_form_answers_with_the_count_the_default_lineup_needs`
   and :func:`test_list_form_answers_with_the_count_a_per_test_override_needs`
   both exist, and why
   :func:`test_list_form_re_derives_the_count_on_every_invoke` changes the
   lineup *between two invokes on one instance* — the only shape that states
   "per invoke" rather than merely "after install".
2. **The strict form's drain guard is load-bearing at exactly three call
   sites** — the retry/coercion contract tests in
   ``tests/test_slice3_names.py`` and ``tests/test_configurable_lineup.py``,
   whose whole subject is *how many times the model was called and what it
   returned each time*. If a later change made that queue quietly permissive,
   those three would keep passing while measuring nothing at all.
3. **The two forms must be routed differently, and nothing enforced it.** The
   safety constraint of the entire design is that *the ``outputs=`` form is
   never routed to the permissive fake* — that constraint is what contains the
   risk that a permissive fake masks a real defect in ``_generate_names``. It
   lived in prose only.
   :func:`test_the_two_call_forms_install_two_different_fakes` turns it into a
   check.

The remaining tests pin the properties that other tests silently depend on:

* **Determinism.** ``tests/test_dual_mode_smoke.py`` compares the two execution
  modes' public logs **byte-for-byte**. That comparison only means anything if
  the same pool and count yield the same names in both modes, every time — so
  ``_pooled_names`` being a pure function is a real contract, not an
  implementation detail.
* **The extension suffix is deliberately NOT ``Player-{k}``.** Slice 3 is about
  to add "no placeholder reached the table" assertions, which can only tell a
  fixture-extended roster from a production-COERCED one while the two shapes
  stay distinguishable.
  :func:`test_pooled_names_extension_never_looks_like_a_production_placeholder`
  asserts against ``_coerce_to_count``'s real output rather than a hand-copied
  pattern, so it proves the discriminator actually discriminates.
* **Case-insensitive dedup and stripping**, because
  :class:`graphia.llm.Roster`'s validator rejects blanks and case-insensitive
  duplicates — a pool the fixture failed to clean would blow up inside the fake
  rather than in the test that supplied it.
* **Prefix-safety (Task 3.3).** No name in a returned roster may be a prefix of
  another. Slice 2's own extension scheme broke this — it recycled a short pool
  with a generation suffix, seating ``Aarav-2`` beside ``Aarav`` — and the
  production vote-target resolver, which matches a needle as a case-insensitive
  **substring**, then refused to act on ``Aar``. Section 5 pins the invariant as
  a property sweep over four pool sizes and eight counts, plus one test that
  drives the real resolver over a real seven-AI roster, so the next extension
  scheme cannot reintroduce the collision quietly.

Everything here is pure and offline: ``load_config`` reads environment
variables and runs pure validation, ``_pooled_names`` is a pure function, and
no fake is ever driven by a graph. No model, no network, no RNG.
"""

from __future__ import annotations

import re

import pytest

# The subjects under test live in the suite's own conftest; ``from conftest
# import ...`` is this repo's established idiom for reaching them (see
# ``tests/test_lineup_recording.py``, ``tests/test_outcome_tracking.py``).
from conftest import (
    _EXTENSION_RESERVE,
    FakeSmall,
    _PooledSmall,
    _pooled_names,
)

import graphia.nodes.setup as setup_nodes
from graphia.config import load_config
from graphia.llm import Roster
from graphia.nodes.day import _fuzzy_match_alive
from graphia.nodes.setup import _coerce_to_count, ai_name_count
from graphia.state import PlayerState

# A pool deliberately SHORTER than any lineup here needs, so almost every test
# exercises the extension path as well as the truncation path.
POOL = ["Ivy", "Marco", "Priya"]

# The lineup pair under test, plus the other ``GRAPHIA_*`` vars whose guards run
# *before* the lineup block in ``load_config`` — a developer's real environment
# could otherwise set, e.g., ``GRAPHIA_REMOTE=1`` and trip the missing-runtime-URL
# guard before validation ever reaches the counts. Copied from
# ``tests/test_lineup_config.py``'s ``_LINEUP_ENV_VARS`` for the same reason.
_LINEUP_ENV_VARS = (
    "GRAPHIA_NUM_CITIZENS",
    "GRAPHIA_NUM_MAFIA",
    "GRAPHIA_MAX_DAYS",
    "GRAPHIA_CONTEXT_WINDOW",
    "GRAPHIA_CONTEXT_TOKEN_BUDGET",
    "GRAPHIA_LLM_PROVIDER",
    "GRAPHIA_REMOTE",
    "GRAPHIA_RUNTIME_URL",
    "GRAPHIA_ROLE",
)


@pytest.fixture(autouse=True)
def lineup_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test in this module from a lineup-neutral environment.

    Required by the default-lineup test, which is only meaningful if the
    lineup env really is unset; harmless and clarifying for the rest.
    """
    for var in _LINEUP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _bound(fake: FakeSmall | _PooledSmall):
    """Bind the schema exactly the way production does.

    The real call is ``get_small().with_structured_output(Roster).invoke(msgs)``,
    so the tests go through ``with_structured_output`` rather than poking
    ``.invoke`` on the bare fake — the binding step is part of the surface the
    82 call sites exercise.
    """
    return fake.with_structured_output(Roster)


# ==========================================================================
# 1. The list form answers with the count the PRODUCTION code asked for
# ==========================================================================


def test_list_form_answers_with_the_count_the_default_lineup_needs(
    fake_small,
) -> None:
    """At the default lineup, the pooled fake returns exactly ``ai_name_count``.

    The expectation is read from :func:`graphia.nodes.setup.ai_name_count` — the
    single owner of the arithmetic — and never from a literal: tech-spec 042
    §2.4 leaves exactly ONE test owning the default's value
    (``test_lineup_config.py``'s defaults test), and a second literal here is
    precisely how a lineup sweep leaves a self-contradictory suite.

    This test **cannot** distinguish an invoke-time derivation from an
    import-time one — both answer correctly at the default. That is the whole
    reason its override sibling below exists, and the reason this one is not
    sufficient on its own.
    """
    expected = ai_name_count(load_config())

    fake = fake_small(POOL)
    roster = _bound(fake).invoke([])

    assert isinstance(roster, Roster)
    assert len(roster.names) == expected
    assert fake.call_count == 1


@pytest.mark.parametrize(
    "num_citizens,num_mafia,expected_names",
    [
        # 5 seats at the table, one of them the human → 4 AI names.
        pytest.param(4, 1, 4, id="4citizens_1mafia_small_table"),
        # 12 seats — ``_MAX_TABLE_SIZE`` exactly — → 11 AI names, which is
        # ``graphia.llm._MAX_AI_NAMES``, so this arm also proves the fake can
        # answer at the largest roster the schema will accept.
        pytest.param(9, 3, 11, id="9citizens_3mafia_table_cap"),
    ],
)
def test_list_form_answers_with_the_count_a_per_test_override_needs(
    monkeypatch: pytest.MonkeyPatch,
    fake_small,
    num_citizens: int,
    num_mafia: int,
    expected_names: int,
) -> None:
    """A per-test ``monkeypatch.setenv`` override is honoured, not the default.

    This is the arm an import-time or ``__init__``-time derivation FAILS. Two
    details are load-bearing and must not be "tidied":

    * **The fake is installed BEFORE the env is overridden.** Moving the
      ``setenv`` calls above the ``fake_small(...)`` call would let a count
      derived in ``_PooledSmall.__init__`` see the override too, and silently
      disarm the check. Installing first is also what UI-driven tests do in
      practice, where the fixture is wired up long before the graph reaches
      ``generate_roster``.
    * **``expected_names`` is a literal the test constructs**, not a config
      echo — "12 seats, one human, therefore 11 generated names". It encodes no
      default, so no lineup change can invalidate it, and it would catch an
      off-by-one that a ``ai_name_count(load_config())``-shaped expectation
      would mirror rather than notice.
    """
    fake = fake_small(POOL)

    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(num_citizens))
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", str(num_mafia))

    roster = _bound(fake).invoke([])

    assert len(roster.names) == expected_names
    # Cross-check against the production helper the fake is supposed to be
    # calling, so the two statements of the count are shown to agree.
    assert expected_names == ai_name_count(load_config())


def test_list_form_re_derives_the_count_on_every_invoke(
    monkeypatch: pytest.MonkeyPatch, fake_small
) -> None:
    """One instance, two lineups, two different answers.

    The sharpest statement of the property: the count is derived **per invoke**,
    not once per install. A derivation cached anywhere — at import, in
    ``__init__``, or memoised on the instance after the first call — makes the
    second assertion fail.
    """
    fake = fake_small(POOL)
    bound = _bound(fake)

    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "4")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")
    first = bound.invoke([])
    assert len(first.names) == 4

    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "9")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "3")
    second = bound.invoke([])
    assert len(second.names) == 11

    assert fake.call_count == 2


def test_list_form_never_drains_and_still_counts_its_calls(fake_small) -> None:
    """Repeated invocations neither raise nor diverge, and ``call_count`` counts.

    Both halves matter. "No drain, no exhaustion" is what removed the 62-site
    scaffolding sweep — a second roster generation must not starve a queue that
    no longer exists. And ``call_count`` must keep incrementing, because
    "generated exactly once" is an assertion 82 call sites are entitled to keep
    making explicitly (fixture docstring, tech-spec 042 §2.2).
    """
    fake = fake_small(POOL)
    bound = _bound(fake)

    first = bound.invoke([])
    second = bound.invoke([])
    third = bound.invoke([])

    # Idempotent: every call answers, and answers identically.
    assert first.names == second.names == third.names
    assert fake.call_count == 3


# ==========================================================================
# 2. The ``outputs=`` form keeps its strict drain guard
# ==========================================================================


def test_outputs_form_still_raises_once_its_queue_is_drained(fake_small) -> None:
    """The strict queue serves what was scripted, then refuses to invent more.

    This guard is what makes the three retry/coercion contract sites able to
    catch a real defect in ``_generate_names`` — "the corrective retry ran once
    and then stopped" is expressed as *the queue holds exactly two entries*. A
    permissive strict form would leave those three tests green and vacuous.
    """
    scripted = Roster(names=["Alba", "Bruno"])
    fake = fake_small(outputs=[scripted])
    bound = _bound(fake)

    assert bound.invoke([]).names == ["Alba", "Bruno"]

    with pytest.raises(AssertionError, match="more times than scripted"):
        bound.invoke([])

    # The refused call is still counted, so a call-count assertion sees the
    # over-invocation even when the AssertionError is swallowed upstream.
    assert fake.call_count == 2


# ==========================================================================
# 3. The two forms are ROUTED differently — the safety constraint
# ==========================================================================


def test_the_two_call_forms_install_two_different_fakes(fake_small) -> None:
    """The list form gets the pooled fake; ``outputs=`` gets the strict one.

    The constraint tech-spec 042 §2.2 calls the thing that makes the whole
    approach safe — *the ``outputs=`` form must never be routed to the
    permissive fake* — with nothing enforcing it until now. Asserted on two
    axes, because either alone is weak:

    * the returned object's **exact type** (``type(...) is``, not
      ``isinstance``, so a future subclass cannot blur the two), and
    * the object actually bound at the ``graphia.nodes.setup.get_small`` call
      site, which is what "routed" means — a fixture that built the right fake
      and then patched something else would pass a type-only check.
    """
    pooled = fake_small(POOL)
    assert type(pooled) is _PooledSmall
    assert setup_nodes.get_small() is pooled

    strict = fake_small(outputs=[Roster(names=["Alba"])])
    assert type(strict) is FakeSmall
    assert setup_nodes.get_small() is strict


def test_fixture_rejects_both_call_forms_at_once(fake_small) -> None:
    """Passing ``names`` AND ``outputs=`` raises instead of one silently winning.

    Silently preferring either form would route a strict-form intent to the
    permissive fake (or vice versa) without a word — the exact confusion the
    two-forms split exists to prevent.
    """
    with pytest.raises(TypeError, match="never both"):
        fake_small(POOL, outputs=[Roster(names=["Alba"])])


def test_fixture_rejects_neither_call_form(fake_small) -> None:
    """Calling ``fake_small()`` bare is an error, not an empty-pool fake."""
    with pytest.raises(TypeError, match="requires either"):
        fake_small()


# ==========================================================================
# 4. ``_pooled_names`` — the pure name supply behind the list form
# ==========================================================================


def test_pooled_names_is_deterministic() -> None:
    """Same pool, same count ⇒ same names. Always.

    ``tests/test_dual_mode_smoke.py`` compares the local and remote execution
    modes' public logs **byte-for-byte**; that assertion is only meaningful
    while both modes derive an identical roster from an identical pool. So this
    is a contract, not an implementation detail. Checked across separate
    ``_PooledSmall`` instances too, since the extension scheme must not depend
    on any per-instance counter.
    """
    assert _pooled_names(POOL, 7) == _pooled_names(POOL, 7)

    first = _PooledSmall(POOL)
    second = _PooledSmall(POOL)
    assert _bound(first).invoke([]).names == _bound(second).invoke([]).names


def test_pooled_names_extension_never_looks_like_a_production_placeholder() -> None:
    """A fixture-extended name can never be mistaken for a coerced ``Player-N``.

    Slice 3 adds "no placeholder reached the table" assertions, and they can
    only work while the two shapes stay distinguishable. The pattern is
    validated against :func:`graphia.nodes.setup._coerce_to_count`'s REAL
    output rather than a hand-copied regex, so this test proves the
    discriminator actually discriminates — if production ever changed its
    padding shape to match the fixture's suffix, the second half goes red and
    says so.
    """
    placeholder = re.compile(r"Player-\d+")

    extended = _pooled_names(POOL, 11)
    assert len(extended) == 11
    assert extended[: len(POOL)] == POOL
    assert not [n for n in extended if placeholder.fullmatch(n)]

    # The pattern is not vacuous: production padding really does produce it.
    coerced = _coerce_to_count(Roster(names=list(POOL)), 11)
    assert [n for n in coerced.names if placeholder.fullmatch(n)]


def test_pooled_names_dedups_case_insensitively_and_strips() -> None:
    """Blanks, whitespace and case-duplicates are cleaned out of the pool.

    Not cosmetic: :class:`graphia.llm.Roster`'s validator rejects a blank name
    and rejects case-insensitive duplicates, so an uncleaned pool would raise
    *inside the fake* — a confusing failure a long way from the test that
    supplied the list. The ``Roster(...)`` construction at the end is the
    assertion that actually pins the reason.
    """
    messy = ["Ivy", " ivy ", "IVY", "   ", "Marco"]

    names = _pooled_names(messy, 3)

    # Three of the five entries were dropped, so the third name is the first
    # extension the reserve supplies. Read from ``_EXTENSION_RESERVE`` rather
    # than hard-coded, so this test keeps stating "a duplicate was dropped and
    # made up from the reserve" and not "the reserve's first entry is X".
    assert names == ["Ivy", "Marco", _EXTENSION_RESERVE[0]]
    # The invariant the dedup exists for: the result is a constructible Roster.
    assert Roster(names=names).names == names


@pytest.mark.parametrize("count", [1, 2, 4, 6, 7, 11])
def test_pooled_names_result_is_always_a_valid_roster(count: int) -> None:
    """Whatever the count, the result is exactly ``count`` names the schema accepts.

    Sweeps the truncation path (``count`` below the pool size), the exact-fit
    path, and the extension path up to ``graphia.llm._MAX_AI_NAMES``. Mirrors
    ``test_configurable_lineup.py``'s ``_coerce_to_count`` sweep, against the
    fixture's supply instead of production's.
    """
    for pool in (["Solo"], POOL, [*POOL, "Silas", "Yuki"]):
        names = _pooled_names(pool, count)
        assert len(names) == count
        assert len({n.lower() for n in names}) == count
        assert Roster(names=names).names == names


# ==========================================================================
# 5. The prefix invariant (Task 3.3) — the property a future extension
#    scheme must not silently break again
# ==========================================================================

# Pools spanning the shapes the suite actually supplies, plus the degenerate
# one-name pool that forces the deepest walk into the reserve (at count 11 it
# needs ten extension names — the worst case the reserve is sized for). The two
# six-name entries are verbatim the two pools ~23 test modules share as
# ``AI_NAMES``, so the sweep covers the exact roster the regression fired on.
_PROPERTY_POOLS: dict[str, list[str]] = {
    "one_name": ["Solo"],
    "three_names": list(POOL),
    "suite_pool_aarav": ["Aarav", "Bianca", "Chiko", "Daria", "Elias", "Finn"],
    "suite_pool_ivy": ["Ivy", "Marco", "Priya", "Silas", "Yuki", "Aarav"],
}


@pytest.mark.parametrize("pool_id", sorted(_PROPERTY_POOLS))
@pytest.mark.parametrize("count", [1, 2, 3, 5, 6, 7, 8, 11])
def test_pooled_names_is_always_prefix_free(pool_id: str, count: int) -> None:
    """No name in a returned roster is a prefix of another. Any pool, any count.

    The invariant Task 3.3 establishes, swept over four pool sizes and eight
    counts so it covers truncation, exact fit, one-short (the seven-seat case
    that broke) and the deepest extension the reserve supports.

    **Why it is a contract and not tidiness.** The production vote-target
    resolver :func:`graphia.nodes.day._fuzzy_match_alive` matches a needle as a
    case-insensitive **substring** of an alive player's name and returns
    ``None`` when two players match, so two names where one opens the other are
    unaddressable by any needle short enough to be convenient. Slice 2's
    extension scheme created exactly that pair by construction — it recycled a
    short pool with a generation suffix, so ``Aarav`` sat next to ``Aarav-2``
    — and at seven AI seats
    ``tests/test_slice7_vote.py::test_human_vote_bumps_human_votes_called``
    failed with ``prefix 'Aar' is ambiguous across alive roster [...]``.
    *Intermittently*, which is worse: which AI is dealt Mafia is an unseeded
    RNG decision (architecture §6), so the collision only bit on the runs where
    the surviving Mafia AI happened to be ``Aarav`` or its recycled twin.

    The pairwise check is written out here rather than importing
    ``conftest._prefix_free``: a test that reuses the implementation's own
    predicate proves only that the predicate agrees with itself.
    """
    pool = _PROPERTY_POOLS[pool_id]

    names = _pooled_names(pool, count)

    assert len(names) == count
    lowered = [n.lower() for n in names]
    offenders = [
        (a, b)
        for i, a in enumerate(lowered)
        for j, b in enumerate(lowered)
        if i != j and a.startswith(b)
    ]
    assert not offenders, (
        f"pool {pool!r} at count {count} produced names where one opens "
        f"another: {offenders!r} — the production substring matcher cannot "
        f"tell such a pair apart. Full roster: {names!r}"
    )
    # The invariant is only worth having on a roster the schema accepts.
    assert Roster(names=names).names == names


@pytest.mark.parametrize("pool_id", ["suite_pool_aarav", "suite_pool_ivy"])
def test_pooled_names_extension_keeps_short_vote_needles_unique(
    pool_id: str,
) -> None:
    """The concrete failure: ``/vote <first three letters>`` resolves uniquely.

    The sharper, end-of-the-chain statement of the invariant above, and the one
    that would have caught the regression directly. Rather than restating the
    matching rule, it drives the **production** resolver
    :func:`graphia.nodes.day._fuzzy_match_alive` over a real seven-AI roster
    plus the human seat — the shape ``tests/test_slice7_vote.py`` builds — and
    asserts every player is reachable by their own first three characters.

    Note the ``+ 1``: the human's name is part of the alive roster the matcher
    searches, so a reserve name colliding with ``Alice`` would be just as
    broken as one colliding with a pool name. That is why
    :data:`conftest._EXTENSION_RESERVE`'s entries are kept distinct in their
    first three characters from the human's name too, not only from each
    other's.
    """
    human_name = "Alice"
    names = [human_name, *_pooled_names(_PROPERTY_POOLS[pool_id], 7)]
    players = {
        f"p-{i}": PlayerState(
            id=f"p-{i}",
            name=name,
            role="law_abiding",
            is_human=(name == human_name),
        )
        for i, name in enumerate(names)
    }

    for player_id, player in players.items():
        needle = player.name[:3]
        assert _fuzzy_match_alive(players, needle) == player_id, (
            f"needle {needle!r} for {player.name!r} did not resolve uniquely "
            f"across the roster {names!r}"
        )


def test_pooled_names_rejects_an_empty_pool() -> None:
    """A pool with nothing usable in it is a loud error, not a silent pad.

    There is nothing to extend from, and inventing names here would put the
    fixture in the business of producing placeholders — exactly what the
    ``Player-N`` distinction exists to keep out of the fixture's output.
    """
    with pytest.raises(AssertionError, match="empty name pool"):
        _pooled_names(["", "   "], 3)
