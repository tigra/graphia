"""Lineup-config tests for spec 014 (Configurable Role Counts), Slice 1.

Covers the new ``GraphiaConfig`` lineup surface introduced in Task 1:

- ``num_citizens`` (env ``GRAPHIA_NUM_CITIZENS``, default ``5``) and
  ``num_mafia`` (env ``GRAPHIA_NUM_MAFIA``, default ``2``), both parsed by
  ``_parse_count`` (unset / empty-string → default; non-numeric → a
  ``SystemExit`` that names the offending var);
- the fail-fast validation block in ``load_config`` — every invalid lineup
  raises ``SystemExit`` with the broken rule named: no Mafiosos
  (``num_mafia < 1``), no Citizens (``num_citizens < 1``), Mafia at or above
  Citizen parity (``num_mafia >= num_citizens``), and a table that exceeds
  ``_MAX_TABLE_SIZE``.

All tests are config-only and offline: ``load_config`` reads env vars and
runs pure validation, so no LLM client is ever constructed and the autouse
``safe_llm`` fixture is never tripped.

Following the ``test_llm_provider_config.py`` convention, each test starts
from the developer's real environment and explicitly sets/deletes only the
env vars under test — the module-autouse ``lineup_env_clean`` fixture wipes
the lineup pair plus the other ``GRAPHIA_*`` vars that could otherwise trip
an unrelated guard in ``load_config`` before the lineup block runs.
"""

from __future__ import annotations

import pytest

from graphia.config import (
    _DEFAULT_NUM_CITIZENS,
    _DEFAULT_NUM_MAFIA,
    _MAX_TABLE_SIZE,
    load_config,
)

# The lineup vars under test, plus the other GRAPHIA_* vars whose guards run
# *before* the lineup block in ``load_config`` (provider / remote pair). A
# developer's ``.env`` could otherwise set, e.g., GRAPHIA_REMOTE=1 and fail
# the missing-runtime-URL guard before validation reaches the lineup.
_LINEUP_ENV_VARS = (
    "GRAPHIA_NUM_CITIZENS",
    "GRAPHIA_NUM_MAFIA",
    "GRAPHIA_LLM_PROVIDER",
    "GRAPHIA_REMOTE",
    "GRAPHIA_RUNTIME_URL",
    "GRAPHIA_ROLE",
)


@pytest.fixture(autouse=True)
def lineup_env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test in this module from a lineup-neutral environment."""
    for var in _LINEUP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. Defaults
# ---------------------------------------------------------------------------


def test_defaults_when_lineup_env_unset() -> None:
    """Nothing set → the documented 5 Citizens / 2 Mafia defaults."""
    cfg = load_config()

    assert cfg.num_citizens == _DEFAULT_NUM_CITIZENS == 5
    assert cfg.num_mafia == _DEFAULT_NUM_MAFIA == 2


def test_empty_string_env_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty / blank env values are treated as unset (``_parse_count``)."""
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "   ")

    cfg = load_config()

    assert cfg.num_citizens == _DEFAULT_NUM_CITIZENS
    assert cfg.num_mafia == _DEFAULT_NUM_MAFIA


# ---------------------------------------------------------------------------
# 2. Valid custom lineups
# ---------------------------------------------------------------------------


def test_valid_custom_lineup_parses_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small valid lineup flows verbatim onto the config."""
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "4")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")

    cfg = load_config()

    assert (cfg.num_citizens, cfg.num_mafia) == (4, 1)


def test_valid_lineup_near_cap_parses_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large valid lineup just under the cap (8 + 3 = 11) parses."""
    assert 8 + 3 <= _MAX_TABLE_SIZE  # guards the fixture against a cap drop
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "8")
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "3")

    cfg = load_config()

    assert (cfg.num_citizens, cfg.num_mafia) == (8, 3)


# ---------------------------------------------------------------------------
# 3. Fail-fast — each invalid lineup names the broken rule
# ---------------------------------------------------------------------------


def test_zero_mafia_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``num_mafia < 1`` — a game with no Mafiosos is already over."""
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "0")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_NUM_MAFIA" in message
    assert "at least 1" in message


def test_negative_mafia_rejected_by_min_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative count parses then fails the ``< 1`` guard, not the parser."""
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "-1")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_NUM_MAFIA" in message
    assert "at least 1" in message
    # Confirms the min-guard fired, not the non-numeric parser.
    assert "whole number" not in message


def test_zero_citizens_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``num_citizens < 1`` — there must be at least one Citizen."""
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", "0")
    # Keep mafia valid-on-its-own so this is unambiguously the citizen guard.
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "1")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "GRAPHIA_NUM_CITIZENS" in message
    assert "at least 1" in message


@pytest.mark.parametrize(
    "citizens, mafia",
    [
        pytest.param("4", "4", id="equal_parity"),
        pytest.param("3", "5", id="mafia_outnumber_citizens"),
    ],
)
def test_mafia_at_or_above_citizen_parity_rejected(
    monkeypatch: pytest.MonkeyPatch, citizens: str, mafia: str
) -> None:
    """``num_mafia >= num_citizens`` — Mafia start at game-winning parity."""
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", citizens)
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", mafia)

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    assert "strictly fewer" in str(exc_info.value)


def test_total_over_cap_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``num_citizens + num_mafia > _MAX_TABLE_SIZE`` — table too large.

    The citizen count is derived from the real cap so a cap change can't
    silently let this lineup slip back under the limit.
    """
    over_cap_citizens = _MAX_TABLE_SIZE + 8  # 20 at the current cap of 12
    monkeypatch.setenv("GRAPHIA_NUM_CITIZENS", str(over_cap_citizens))
    monkeypatch.setenv("GRAPHIA_NUM_MAFIA", "2")

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "Table too large" in message
    assert str(_MAX_TABLE_SIZE) in message


@pytest.mark.parametrize(
    "var, raw",
    [
        pytest.param("GRAPHIA_NUM_MAFIA", "abc", id="mafia_alpha"),
        pytest.param("GRAPHIA_NUM_CITIZENS", "2.5", id="citizens_float"),
    ],
)
def test_non_numeric_count_rejected(
    monkeypatch: pytest.MonkeyPatch, var: str, raw: str
) -> None:
    """A non-integer value is a ``SystemExit`` from ``_parse_count``."""
    monkeypatch.setenv(var, raw)

    with pytest.raises(SystemExit) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert var in message
    assert "whole number" in message
    assert raw in message
