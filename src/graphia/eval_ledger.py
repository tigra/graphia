"""Pure, Textual-free data layer for the eval-ledger viewer (spec 012, Slice 1).

Parses the repo-committed quality ledger (``evals/blunder-ledger.yaml`` — one
``---``-separated YAML document per run, written by spec 011's
``blunder_eval``) and flattens each *heterogeneous* record into one stable
table model. This is the increment that finally takes on the YAML-parser
dependency 011 deliberately deferred (``evals/README.md``): the ledger is read
with ``yaml.safe_load_all`` — multi-document, data-only, **no object
construction**, so it is read-only by construction.

**No Textual import lives here on purpose.** All of the parsing, the column
model, the cell formatting, and the per-row search blobs are unit-testable
without driving a TUI; the thin Textual viewer (a later task) consumes the
:class:`TableModel` this module emits and adds the Rich/`DataTable` presentation
on top. The pure layer emits **plain strings** — Rich stays a UI concern.

**Heterogeneity is absorbed here (the headline risk, tech-spec 012 §3).** The
committed ledger already mixes shapes: early *pre-provenance* records carry no
``code`` block, no ``settings`` block, no ``ci_low``/``ci_high``, and put the
game count under ``run.games``; later records carry the full ``code`` /
``settings`` / CI blocks and ``settings.games``. Every field read goes through
:func:`_dig`, a defensive dotted-get with a default — a ``KeyError`` from a
missing nesting level (or a missing nested ``metrics.self_vote.initiation``)
must never reach the UI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "RawRecord",
    "LedgerParseError",
    "METRIC_ORDER",
    "SEARCH_FIELDS",
    "SEARCH_SCOPE_ALL",
    "TableModel",
    "TranscriptEntry",
    "TRANSCRIPTS_DIRNAME",
    "load_ledger",
    "build_table_model",
    "render_detail",
    "row_matches_field",
    "transcript_dir_for",
    "list_transcripts",
    "read_transcript",
    # Every transcript kind constant, in the order they were introduced. Kept
    # complete on purpose (spec 038, Slice 4): the list used to carry only
    # ``KIND_MARKER`` / ``KIND_PLAIN`` while the UI imported the other six
    # regardless — a half-populated export nobody chose. A slice that adds a
    # kind adds its constant here as well as to :data:`TRANSCRIPT_KINDS`.
    "KIND_MARKER",
    "KIND_PLAIN",
    "KIND_ATTR",
    "KIND_FIELD_LABEL",
    "KIND_SPEAKER",
    "KIND_SPEECH",
    "KIND_THOUGHT",
    "KIND_RECAP",
    "KIND_SPEAKER_MAFIA",
    "KIND_SPEAKER_LAW_ABIDING",
    "KIND_SPEECH_MAFIA",
    "KIND_SPEECH_LAW_ABIDING",
    "KIND_ATTR_MAFIA",
    "KIND_ATTR_LAW_ABIDING",
    "KIND_SPEAKER_HUMAN",
    "KIND_SPEECH_HUMAN",
    "KIND_SPEAKER_MAFIA_HUMAN",
    "KIND_SPEAKER_LAW_ABIDING_HUMAN",
    "KIND_SPEECH_MAFIA_HUMAN",
    "KIND_SPEECH_LAW_ABIDING_HUMAN",
    "KIND_DIARY",
    "TRANSCRIPT_KINDS",
    "tokenize_transcript",
]

# One parsed ledger record: the YAML document as a plain nested mapping. Keyed by
# the fixed top-level order ``run`` / ``code`` / ``provider`` / ``settings`` /
# ``quality`` / ``metrics`` / ``notes`` — but any of those (and any field within)
# may be absent on a pre-provenance record, which is why every read is defensive.
RawRecord = dict[str, Any]


class LedgerParseError(Exception):
    """The ledger file exists but is not valid YAML.

    Raised by :func:`load_ledger` so the viewer can surface a friendly message
    instead of letting a raw ``yaml.YAMLError`` traceback escape. A *missing* or
    *empty* file is **not** an error (it yields ``[]``); only genuinely
    malformed YAML lands here.
    """


# The canonical metric column order — the single source of truth for which
# behaviour columns appear and in what order, in the harness family order
# (``blunder_eval``'s detector family: the two speech metrics, then the
# self/peer × initiation/yes vote family). Each entry is a
# ``(dotted_key, header_label)`` pair: the dotted key resolves the metric inside
# a record's nested ``metrics`` map via :func:`_dig` (``self_vote.initiation``
# is the nested ``metrics.self_vote.initiation`` facet map), and the label is the
# concise column header. A future 011 metric surfaces as one appended tuple — no
# other change needed (column count, headers, and per-row cells all derive from
# this tuple).
METRIC_ORDER: tuple[tuple[str, str], ...] = (
    ("repetition", "repetition"),
    ("third_person_self_talk", "self-talk"),
    ("self_vote.initiation", "self-vote init"),
    ("self_vote.yes", "self-vote yes"),
    ("peer_vote.initiation", "peer-vote init"),
    ("peer_vote.yes", "peer-vote yes"),
    # Spec 031: the persona-distinctiveness metric — a near-duplication rate over a
    # run's generated AI personas (higher = personas more alike = less distinct).
    # Surfaced as one appended tuple; ``render_detail`` and the viewer table pick it
    # up automatically (column count, headers, and cells all derive from this tuple).
    ("persona_near_dup", "persona near-dup"),
    # Spec 031 (additive): the continuous LEXICAL companion — the MEAN pairwise
    # lexical similarity of a run's AI personas (higher = personas more alike). A
    # mean-type facet (``mean``/``denominator``, no ``rate``/``count``), rendered as
    # ``~<mean> (n=<pairs>)`` by ``_metric_cell`` / ``render_detail``.
    ("persona_lex_mean", "persona lex mean"),
    # Spec 032 (additive): the LEXICAL PEAK companion — the most-similar-pair (MAX)
    # pairwise lexical similarity of a run's AI personas (flags a collapsed pair the
    # mean smooths over). A value-type facet (``peak``/``denominator``, no
    # ``rate``/``count``), rendered as ``~<peak> (n=<pairs>)`` by ``_metric_cell`` /
    # ``render_detail`` — the same value-type branch as the mean (which keys off
    # ``mean`` OR ``peak``).
    ("persona_lex_peak", "persona lex peak"),
    # Spec 033 (additive): the SEMANTIC (meaning-based) MEAN companion — the MEAN
    # pairwise *cosine* of a run's AI persona embeddings (a Bedrock Titan v2
    # measuring instrument; higher = personas more alike in KIND, not wording).
    # A value-type facet (``mean``/``denominator``, no ``rate``/``count``),
    # rendered by the SAME value-type branch as the lexical mean/peak —
    # ``~<mean> (n=<pairs>)`` — so no new render code is needed. Omitted (blank)
    # when embeddings were unavailable for the run (e.g. an ollama run, no creds).
    ("persona_sem_mean", "persona sem mean"),
    # Spec 033 (additive): the SEMANTIC PEAK companion — the most-similar-pair (MAX)
    # pairwise *cosine* of a run's AI persona embeddings (the semantic parallel of
    # the lexical peak; flags a meaning-collapsed pair the cosine mean smooths over).
    # A value-type facet (``peak``/``denominator``, no ``rate``/``count``), rendered
    # by the SAME value-type branch — ``~<peak> (n=<pairs>)``. Omitted (blank) under
    # the same gate as the semantic mean (embeddings unavailable for the run).
    ("persona_sem_peak", "persona sem peak"),
)

# Fixed leading column headers, before the per-metric columns (tech-spec 012
# §2.3, extended by 013 §2.3). ``⚠`` is the dirty-working-copy marker column; the
# rest are the run's identifying facts. ``Wins (LA/M)`` (:func:`_outcomes_cell`)
# and ``Votes (LA/M)`` (:func:`_vote_activity_cell`) are the two game-dynamics
# columns — both compact, both *fixed* (head) columns appended **before**
# ``Notes`` / the metric block so the UI's right-justify split
# (``len(columns) - len(METRIC_ORDER)``) keys off the *tail* and keeps tracking
# the metric count: the new head columns stay left-justified like the other
# identity columns. ``Notes`` is the run's free-text note (truncated to a single
# bounded line by :func:`_note_cell`) — it lives here, *before* the metric block,
# for two reasons: it keeps that right-justification split undisturbed, and —
# because notes are part of the search blob (§2.4) — it makes a note-match
# *visible* in the row instead of looking like a phantom hit (the full verbatim
# note is in the drill-down). The 6 wide metric columns scroll off-screen
# regardless, so placing Notes ahead of them keeps it in the initial viewport at
# no cost to the metrics.
_FIXED_COLUMNS: tuple[str, ...] = (
    "⚠",
    "Date",
    # Spec 036: which KIND of measurement this record is (``run.kind`` —
    # :func:`_kind_cell`). Grouped with the identity columns, immediately after
    # ``Date``, mirroring the record's own key order (``render_record`` emits
    # ``run.kind`` directly after ``run.date``) and deliberately **not** in the
    # metric tail, so the UI's right-justify split
    # (``len(columns) - len(METRIC_ORDER)``) keys off the metric count and this
    # column stays left-justified like every other identity column — the same
    # spec-029 precedent recorded below.
    "Kind",
    "Provider",
    "Large model",
    "Small model",
    "Games",
    "Wins (LA/M)",
    # Spec 029: three curated game-dynamics columns, added before ``Notes`` so the
    # UI's right-justify metric split (``len(columns) - len(METRIC_ORDER)``) keys
    # off the metric tail and the new head columns stay left-justified like the
    # other identity columns. ``Scripted (side)`` (:func:`_scripted_side_cell`,
    # spec 027) and ``Unres (R/N)`` (:func:`_resolution_cell`, spec 013/023) group
    # next to ``Wins (LA/M)``; ``Stand-in`` (:func:`_stand_in_cell`, spec 026 — a
    # settings fact that DEFAULTS to ``passive`` on pre-026 records, not blank) sits
    # next to ``Lineup`` (the other settings fact).
    "Scripted (side)",
    "Unres (R/N)",
    "Votes (LA/M)",
    "Stand-in",
    # Spec 039: which ARM of the private-diaries A/B a run measured
    # (:func:`_diaries_cell`, reading ``settings.private_diaries_enabled``).
    # Placed between ``Stand-in`` and ``Lineup`` so the three settings facts stay
    # contiguous **in the record's own key order** (``render_record`` emits
    # ``scripted_player`` → ``private_diaries_enabled`` → the nested ``lineup``) —
    # the same mirror-the-record rule that positioned ``Kind`` after ``Date``. It
    # is a *fixed* (head) column, ahead of the metric tail, because diaries is a
    # **setting, not a metric**: ``METRIC_ORDER`` is untouched, and the UI's
    # right-justify split (``len(columns) - len(METRIC_ORDER)``) keys off the
    # metric count, so this column is left-justified like every other identity
    # column and no metric cell moves.
    #
    # It exists so an on/off pair is legible **in the table** — the whole point of
    # the arm label is reading the two arms side by side, and a drill-down-only
    # field makes that a two-screen job. Absence renders **blank**, never ``off``:
    # see :func:`_diaries_cell`.
    "Diaries",
    "Lineup",
    "Notes",
)

# Width cap for the single-line ``Notes`` table cell (the full, multi-line note
# is rendered verbatim in the drill-down — this is only the at-a-glance preview).
_NOTES_CELL_MAXLEN = 50

# The en-dash separating the two CI bounds in a metric cell (``[lo–hi]``) — a
# typographic dash, not a hyphen, matching the tech-spec cell format.
_CI_DASH = "–"

# Sentinel distinguishing "key absent" from a present ``None`` value, so
# :func:`_dig` can keep walking into a level that genuinely holds ``None``
# without mistaking it for a missing key.
_MISSING = object()


def _resolved_large_model(record: RawRecord) -> Any:
    """The effective large-model id — ``settings.large_model ?? provider.large_model``.

    The one place the ``settings.* ?? provider.*`` model fallback is expressed, so
    the row cells, the search blob, and the scoped-search ``model`` field all agree
    on which id actually ran (post-override) for a heterogeneous record.
    """
    return _dig(
        record, "settings.large_model", default=_dig(record, "provider.large_model", "")
    )


def _resolved_small_model(record: RawRecord) -> Any:
    """The effective small-model id — ``settings.small_model ?? provider.small_model``."""
    return _dig(
        record, "settings.small_model", default=_dig(record, "provider.small_model", "")
    )


def _resolved_games(record: RawRecord) -> Any:
    """The effective game count — ``settings.games ?? run.games``.

    Later records moved the count under ``settings.games``; pre-provenance records
    keep it under ``run.games``. One shared resolution so cells and search agree.
    """
    return _dig(record, "settings.games", default=_dig(record, "run.games", ""))


def _outcomes_cell(record: RawRecord) -> str:
    """The ``Wins (LA/M)`` table cell — both side win-rates, or blank when absent.

    Reads ``outcomes.law_abiding.rate`` / ``outcomes.mafia.rate`` via :func:`_dig`
    and renders the compact two-decimal pair ``LA .55 / M .30`` (no CI in the
    table — the band is detail-only, like the metric cells). An **absent**
    ``outcomes`` block (any pre-013 record) → the **empty string**, mirroring the
    absent-metric blank. The ``games == 0`` path emits ``outcomes`` with the rate
    keys omitted, so each side resolves to ``None`` and renders as the dash-less
    placeholder ``LA — / M —`` rather than raising.
    """
    if _dig(record, "outcomes", _MISSING) is _MISSING:
        return ""
    la = _dig(record, "outcomes.law_abiding.rate")
    mafia = _dig(record, "outcomes.mafia.rate")
    return f"LA {_table_rate(la)} / M {_table_rate(mafia)}"


def _table_rate(rate: Any) -> str:
    """A side rate as a leading-dot two-decimal (``0.55`` → ``.55``), or ``—``.

    Drops the leading ``0`` for table width (``.55`` not ``0.55``); a ``None``
    rate (the ``games == 0`` path omits rate keys) shows :data:`_ABSENT` so a
    present-but-rate-less outcomes block stays distinct from a real ``0.0``.
    """
    if rate is None:
        return _ABSENT
    return f"{float(rate):.2f}".lstrip("0") or "0"


def _vote_activity_cell(record: RawRecord) -> str:
    """The ``Votes (LA/M)`` table cell — the explicit-zero, carried to the viewport.

    The deliberate inverse of :func:`_outcomes_cell`'s absent-blank: a
    ``vote_activity`` block that is **present** renders ``LA {n} / M {n}`` even
    when both counts are ``0`` (so the Nova-silent-Day pathology shows
    ``LA 0 / M 0``, never a phantom blank), whereas an **absent** block (a pre-013
    record) renders the **empty string**. The present-zero and absent states MUST
    render differently — that distinction is the whole point of the block's
    explicit-zero guarantee (tech-spec 013 §2.2). Implemented via the ``_MISSING``
    sentinel: block absent → blank; else both ``by_side`` ints (each defaulting to
    ``0``) are formatted.
    """
    if _dig(record, "vote_activity", _MISSING) is _MISSING:
        return ""
    la = _vote_count(_dig(record, "vote_activity.by_side.law_abiding"))
    mafia = _vote_count(_dig(record, "vote_activity.by_side.mafia"))
    return f"LA {la} / M {mafia}"


def _vote_count(value: Any) -> int:
    """A ``by_side`` count coerced to ``int``, defaulting a missing side to ``0``."""
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _lineup_cell(record: RawRecord) -> str:
    """The ``Lineup`` table cell — the configured ``citizens/mafia``, or blank.

    Reads ``settings.lineup.num_citizens`` / ``num_mafia`` via :func:`_dig` and
    renders the compact ``"{c}/{m}"`` pair (e.g. ``5/2``). An **absent**
    ``settings.lineup`` sub-map (any pre-014 record — no migration) → the
    **empty string**, mirroring :func:`_outcomes_cell`'s absent-blank so a
    never-recorded lineup stays distinct from any present value.
    """
    if _dig(record, "settings.lineup", _MISSING) is _MISSING:
        return ""
    citizens = _dig(record, "settings.lineup.num_citizens")
    mafia = _dig(record, "settings.lineup.num_mafia")
    return f"{_text(citizens)}/{_text(mafia)}"


# The compact side abbreviations the table cells use (``LA``/``M``), mapping the
# full side names the ledger records (``law_abiding``/``mafia``). Shared by the
# scripted-side cell so its side label matches the ``LA``/``M`` vocabulary of the
# ``Wins (LA/M)`` / ``Votes (LA/M)`` columns.
_SIDE_ABBR: dict[str, str] = {"law_abiding": "LA", "mafia": "M"}

# The stand-in mode read for a pre-026 record: the field was added in spec 026
# and is read as the prior default (``passive``, the only stand-in that existed
# then) per the ``evals/README.md`` contract — so this column DEFAULTS rather than
# blanks for older records (the one new column that does not blank for absence).
_STAND_IN_DEFAULT = "passive"


def _scripted_side_cell(record: RawRecord) -> str:
    """The ``Scripted (side)`` table cell — the scripted stand-in's-side win rate (spec 027).

    Reads ``outcomes.scripted_side`` via :func:`_dig` and renders the side it
    refers to plus the rate, compactly (e.g. ``LA .55`` / ``M .30``), reusing
    :func:`_table_rate` so the number style matches ``Wins (LA/M)`` (leading-dot
    two-decimal, ``—`` for a rate-less ``games == 0`` block). The side is the
    ``LA``/``M`` abbreviation derived from ``outcomes.scripted_side.side``
    (``law_abiding``/``mafia``).

    An **absent** ``scripted_side`` block — a pre-027 record, or any run that
    resolved no seat side (``_dig(record, "outcomes.scripted_side", _MISSING) is
    _MISSING``) — renders the **empty string**, mirroring :func:`_outcomes_cell`'s
    absent-blank. An unrecognised/absent ``side`` defends to a bare rate (no
    abbreviation prefix) rather than raising.
    """
    if _dig(record, "outcomes.scripted_side", _MISSING) is _MISSING:
        return ""
    side = _dig(record, "outcomes.scripted_side.side")
    abbr = _SIDE_ABBR.get(side, "")
    rate = _table_rate(_dig(record, "outcomes.scripted_side.rate"))
    return f"{abbr} {rate}" if abbr else rate


def _stand_in_cell(record: RawRecord) -> str:
    """The ``Stand-in`` table cell — which human-seat stand-in ran (spec 026).

    Reads ``settings.scripted_player`` via :func:`_dig`, rendering the compact
    label ``active`` / ``passive``. Unlike every other fixed cell, this one
    **defaults** rather than blanks for an absent field: per the
    ``evals/README.md`` record contract, ``scripted_player`` is omitted on pre-026
    records and read as the prior default :data:`_STAND_IN_DEFAULT` (``passive`` —
    the only stand-in that existed then), so an older record reads ``passive``, not
    a blank cell.

    **Kind-aware (spec 036 follow-up).** That defaulting is only correct where
    absence means "the prior default". On a record whose ``run.kind`` is set —
    a persona-bench measurement — there is no human seat at all, so absence
    means *not applicable* and a defaulted ``passive`` would assert something
    untrue. Such a record therefore blanks. The two meanings of an absent field
    are distinguished by kind, not collapsed into one default.
    """
    if _text(_dig(record, "run.kind", default="")):
        return ""
    return _text(_dig(record, "settings.scripted_player", default=_STAND_IN_DEFAULT))


# The two labels the ``Diaries`` cell renders a recorded arm as. Deliberately the
# **CLI vocabulary** the arm is invoked with (``--diaries on|off``) rather than
# the YAML boolean's ``True``/``False``: the column exists to make a pair legible
# at a glance, ``on``/``off`` is what the operator typed, and a table cell reading
# ``False`` looks like a Python repr rather than an arm of an experiment. The
# mapping is **total** — a recorded arm is one bool and lands on exactly one of
# these two — so cell text and on-disk value cannot drift the way a partial label
# lookup (:func:`_kind_cell`'s reason for rendering verbatim) could. The
# drill-down still shows the recorded boolean itself.
_DIARIES_ON = "on"
_DIARIES_OFF = "off"


def _diaries_cell(record: RawRecord) -> str:
    """The ``Diaries`` table cell — which arm of the private-diaries A/B ran (spec 039).

    Reads ``settings.private_diaries_enabled`` via :func:`_dig` and renders
    :data:`_DIARIES_ON` / :data:`_DIARIES_OFF`, so a diaries-on and a diaries-off
    record are readable **as a pair** straight from the table.

    **Absence renders blank — never ``off``**, and this is the one thing the cell
    exists to get right. It is deliberately NOT the defaulting posture of
    :func:`_stand_in_cell` (absent ⇒ the prior default) or :func:`_kind_cell`
    (absent ⇒ "a played game"): a pre-039 record was played by a build with **no
    diary feature at all**, so it is neither arm of the comparison. Rendering it
    ``off`` would assert a measurement nobody made and would silently offer every
    one of the 30 committed records as the control half of an A/B pair. A valid
    pair is two spec-039-era records — same commit, provider, ``run.kind`` and
    ``metrics_version`` — differing only in this field (``evals/README.md``).
    """
    arm = _dig(record, "settings.private_diaries_enabled")
    if arm is None:
        return ""
    return _DIARIES_ON if bool(arm) else _DIARIES_OFF


# The record kind read for a pre-036 record. ``run.kind`` was added in spec 036
# and its ABSENCE is *meaningful*, not unknown: per the ``evals/README.md``
# contract, absent ⇒ a played game (the only kind of measurement that existed
# before). So this column DEFAULTS rather than blanks for older records — the
# same default-not-blank posture as :data:`_STAND_IN_DEFAULT`, and the reason
# every one of the already-committed records reads ``game`` instead of showing a
# column of blanks that would look like a field nobody filled in.
_KIND_DEFAULT = "game"


def _kind_cell(record: RawRecord) -> str:
    """The ``Kind`` table cell — which kind of measurement this record is (spec 036).

    Reads ``run.kind`` through the defensive :func:`_dig` with
    :data:`_KIND_DEFAULT`, so a pre-036 record — and every game run, which omits
    the key entirely — reads ``game`` rather than blank. Absence means "a played
    game", not "not recorded", so this is one of the two cells that *default*
    instead of blanking (:func:`_stand_in_cell` is the other).

    The recorded value is rendered **verbatim**: a bench record reads
    ``persona-bench``, the literal contract value. No label lookup table, for two
    reasons — the cell text, the scoped ``kind`` search field and the on-disk
    value then cannot drift apart, and a kind introduced by a future spec renders
    as itself instead of being silently mislabelled by a map that has not heard
    of it. There is deliberately **no render branch**: one total :func:`_dig`
    plus :func:`_text` is the whole cell, which is what lets the records already
    committed (none of which carry the field) flatten without raising.
    """
    return _text(_dig(record, "run.kind", default=_KIND_DEFAULT))


def _resolution_cell(record: RawRecord) -> str:
    """The ``Unres (R/N)`` table cell — the non-side game-resolution counts.

    Reads ``outcomes.runaway`` (spec 023, the in-game Day-cap hit) and
    ``outcomes.no_winner`` (spec 013) via :func:`_dig`, coerced through
    :func:`_vote_count`, and renders the two "didn't resolve to a side" buckets
    compactly as ``R{n} N{n}`` (e.g. ``R 1 N 2``). Each bucket defaults to ``0``
    when its key is absent but the ``outcomes`` block is present, so a run that
    resolved all games to a side reads the present-zero ``R 0 N 0`` — distinct from
    the absent-block blank.

    An **absent** ``outcomes`` block (any pre-013 record) → the **empty string**,
    the same ``_MISSING`` guard as :func:`_outcomes_cell`. ``draw`` is intentionally
    not shown here (it's derivable from the partition and lives in the detail view);
    keeping the cell to the two unresolved buckets keeps the column narrow.
    """
    if _dig(record, "outcomes", _MISSING) is _MISSING:
        return ""
    runaway = _vote_count(_dig(record, "outcomes.runaway"))
    no_winner = _vote_count(_dig(record, "outcomes.no_winner"))
    return f"R {runaway} N {no_winner}"


def _winner_keyword(record: RawRecord) -> str:
    """The scoped-search ``winner`` keyword for a record (tech-spec 013 §2.3).

    A derived label naming the side that won the **strict majority** of the
    record's completed games (``law_abiding`` / ``mafia``), or ``draw`` when the
    plain ``draw`` bucket leads, or ``mixed`` when no single bucket has a strict
    majority. An **absent** ``outcomes`` block (any pre-013 record) → the empty
    string, so the field neither matches nor pollutes the blob. Every read is
    defensive (:func:`_dig` + :func:`_vote_count`), so a partial/zero-count block
    never raises (``games == 0`` omits rates but keeps the ``wins``/``draw``
    counts, all zero → ``mixed``).
    """
    if _dig(record, "outcomes", _MISSING) is _MISSING:
        return ""
    buckets = {
        "law_abiding": _vote_count(_dig(record, "outcomes.law_abiding.wins")),
        "mafia": _vote_count(_dig(record, "outcomes.mafia.wins")),
        "draw": _vote_count(_dig(record, "outcomes.draw")),
    }
    leader = max(buckets, key=lambda key: buckets[key])
    top = buckets[leader]
    # A strict majority over the other two buckets names the winner; otherwise the
    # run had no decisive side (a tie at the top, or all-zero) → "mixed".
    if top > 0 and top > sum(v for k, v in buckets.items() if k != leader):
        return leader
    return "mixed"


def _lineup_keyword(record: RawRecord) -> str:
    """A ``"5c2m"``-style search keyword for the configured lineup, or blank.

    Reads ``settings.lineup.num_citizens`` / ``num_mafia`` via :func:`_dig` and
    renders a single compact, search-friendly token (``"{c}c{m}m"``) so a
    free-text query like ``5c2m`` finds runs by their lineup. An **absent**
    ``settings.lineup`` (any pre-014 record) → the empty string, so the keyword
    neither matches nor pollutes the blob — the same posture as
    :func:`_winner_keyword`.
    """
    if _dig(record, "settings.lineup", _MISSING) is _MISSING:
        return ""
    citizens = _dig(record, "settings.lineup.num_citizens")
    mafia = _dig(record, "settings.lineup.num_mafia")
    return f"{_text(citizens)}c{_text(mafia)}m"


def _dig(record: Any, dotted_key: str, default: Any = None) -> Any:
    """Defensive dotted-path lookup — never raises on a missing level.

    Walks ``record`` along ``dotted_key`` (``"settings.games"``,
    ``"self_vote.initiation"``), returning ``default`` the moment any level is
    absent or is not a mapping — so a pre-provenance record with no ``settings``
    block, or a record whose ``metrics`` omits a vote family, resolves to the
    default instead of a ``KeyError``. The heart of the heterogeneity absorption
    (tech-spec 012 §2.1): one place every field read is made total.
    """
    current: Any = record
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return default
        nxt = current.get(part, _MISSING)
        if nxt is _MISSING:
            return default
        current = nxt
    return current


# The scopeable search fields, in dropdown order (tech-spec 012 §2.4). The viewer
# offers a field **selector** (defaulting to :data:`SEARCH_SCOPE_ALL`) so the
# maintainer *picks* the field rather than typing its name — there is no
# ``field:value`` text parsing. Each name is both a :func:`_search_fields` key
# (the per-row haystack for that one field) and a selector option; the value the
# maintainer types is matched against the chosen field's text (or the whole blob
# under "All") by :func:`row_matches_field`.
SEARCH_FIELDS: tuple[str, ...] = (
    "provider",
    "date",
    # Spec 036: scope the search to the record KIND, so a reviewer can filter the
    # history down to one kind of measurement (``persona-bench``) or to the
    # played games (``game``) — the whole point of labelling the kind. Carries the
    # same resolved text the ``Kind`` cell shows, defaults included.
    "kind",
    "model",
    "commit",
    "branch",
    "games",
    "note",
    "state",
    "winner",
)

# The selector's default option — search across *all* facts (the free-text blob),
# the no-scope state. Kept distinct from any :data:`SEARCH_FIELDS` name so the
# selector value is unambiguous.
SEARCH_SCOPE_ALL = "All"


@dataclass(frozen=True, slots=True)
class TableModel:
    """The flattened ledger as five **index-parallel** lists.

    ``rows[i]``, ``search_blobs[i]``, ``search_fields[i]`` and ``records[i]`` all
    describe the *same* run — index-parallelism is the contract the UI relies on
    to resolve a selected (or filtered) table row back to its raw record.
    ``columns`` is the shared header list every ``rows[i]`` aligns to.

    - ``columns`` — header labels (the fixed leading columns then one per
      :data:`METRIC_ORDER` entry).
    - ``rows`` — one list of **formatted plain-string cells** per run, aligned to
      ``columns`` (Rich/justification stays a UI concern).
    - ``search_blobs`` — one lowercased, searchable string per run (date,
      provider, both model ids, commit, branch, a ``dirty``/``clean`` keyword,
      and the full notes text), so a free-text substring filter is a flat ``in``
      test. The ``Notes`` column surfaces a (truncated) note in the row so a
      notes match is visible, not a phantom hit.
    - ``search_fields`` — one ``dict[str, str]`` per run mapping a canonical
      :data:`SEARCH_FIELDS` name to that row's **lowercased** searchable text for
      that one field, so a *scoped* search (the field selector set to e.g.
      ``provider``) matches the typed value against only the named field rather
      than the whole blob. Built from the same defensive :func:`_dig` extraction
      as the cells / ``search_blobs``.
    - ``records`` — the raw :data:`RawRecord` backing each row, for the detail
      drill-down.
    """

    columns: list[str]
    rows: list[list[str]]
    search_blobs: list[str]
    search_fields: list[dict[str, str]]
    records: list[RawRecord]


def load_ledger(path: Path) -> list[RawRecord]:
    """Parse the multi-document ledger at ``path`` into a list of raw records.

    Uses ``yaml.safe_load_all`` over the ``---``-separated stream (never
    ``yaml.load`` — the ledger is data-only and this module is strictly
    read-only, so no arbitrary object construction is ever attempted). Behaviour:

    - A **missing** file, or one that is **empty / whitespace-only**, yields
      ``[]`` — an empty ledger is a normal state, not an error.
    - A ``None`` document (e.g. a trailing ``---`` separator, or a blank
      document between two records) is **skipped**.
    - Malformed YAML raises :class:`LedgerParseError` (chained from the
      underlying ``yaml.YAMLError``) so the viewer shows a friendly message
      rather than a traceback.

    Non-mapping documents (a stray scalar or list) are skipped defensively — a
    ledger record is always a mapping, and the flattener downstream assumes one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    if not text.strip():
        return []

    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise LedgerParseError(
            f"Could not parse ledger at {path}: {exc}"
        ) from exc

    return [doc for doc in documents if isinstance(doc, dict)]


# ===========================================================================
# Transcript locating / listing / reading (spec 017, Slice 2 — pure layer).
#
# The viewer holds the ledger ``Path`` and a selected :data:`RawRecord`; from
# those it must reach the run's preserved per-game transcripts. The store lives
# in the ledger's **sibling ``transcripts/`` dir** — exactly the layout
# ``blunder_eval`` writes to (``TRANSCRIPTS_ROOT = LEDGER_PATH.parent /
# "transcripts"``), and a record names its run's dir with the run-id directory
# NAME under ``run.transcript_dir`` (never an absolute path). These three pure
# functions — locate, list, read — are the whole data layer the
# ``DetailScreen``'s transcript panel / ``TranscriptScreen`` consume; **no Textual import**,
# **read-only** (they never create/write/delete), and **defensive throughout**
# (mirroring :func:`_dig`): a missing ``transcript_dir`` field, a dir absent
# locally (a run not shared/pulled), an empty dir, or an unreadable file all
# resolve to an **empty result, never an exception** — which is what drives the
# viewer's "No transcripts for this run." state.
# ===========================================================================

# The fixed sibling-of-the-ledger directory name that holds every run's
# transcript dir — the pure-layer mirror of ``blunder_eval.TRANSCRIPTS_ROOT``'s
# ``LEDGER_PATH.parent / "transcripts"`` layout. Kept as a module constant so the
# locating logic and the writer agree on the one folder name.
TRANSCRIPTS_DIRNAME = "transcripts"

# The glob the run's per-game transcript files match (``game-01.txt`` …), the
# read-side mirror of ``blunder_eval``'s ``game-NN.txt`` naming. Sorting these by
# filename yields the natural ``game-01 … game-NN`` order (zero-padded indices
# sort lexically the same as numerically).
_TRANSCRIPT_GLOB = "game-*.txt"


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One browsable game transcript — a display ``label`` and its file ``path``.

    The unit :func:`list_transcripts` returns (one per ``game-NN.txt`` in a run's
    dir, sorted). ``label`` is the file's stem (``game-01``) — the human-readable
    game name the transcript panel shows; ``path`` is the resolved file,
    so the viewer hands it straight to :func:`read_transcript` with **no path
    arithmetic of its own**. A frozen, slotted value object, matching
    :class:`TableModel`'s house style.
    """

    label: str
    path: Path


def transcript_dir_for(record: RawRecord, ledger_path: Path) -> Path | None:
    """Locate a record's transcript directory, or ``None`` when it has none.

    Reads the record's ``run.transcript_dir`` (the run-id directory NAME, NOT an
    absolute path — what ``blunder_eval.render_record`` writes) via the defensive
    :func:`_dig` getter, and resolves it against the ledger's **sibling
    ``transcripts/`` dir** (``ledger_path.parent / "transcripts" / <run-id>``) —
    mirroring how ``blunder_eval`` derives ``TRANSCRIPTS_ROOT``. Returns the
    :class:`Path` **without checking it exists** (existence is :func:`list_transcripts`'s
    concern); a **missing / empty / non-string** ``transcript_dir`` field (an
    older pre-017 record, or a run that wrote none) resolves to ``None`` so a
    field absence never raises — the locate half of the "no transcripts" state.
    """
    run_id = _dig(record, "run.transcript_dir")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    return ledger_path.parent / TRANSCRIPTS_DIRNAME / run_id


def list_transcripts(record: RawRecord, ledger_path: Path) -> list[TranscriptEntry]:
    """List a run's per-game transcripts as sorted :class:`TranscriptEntry` items.

    Locates the run's dir via :func:`transcript_dir_for`, then returns one
    :class:`TranscriptEntry` per ``game-*.txt`` file in it, **sorted by filename**
    so the natural ``game-01 … game-NN`` order falls out (zero-padded indices sort
    lexically). Each entry carries the file's stem as its ``label`` (``game-01``)
    and the resolved ``path``, so the viewer never does path arithmetic.

    Defensive (mirroring :func:`_dig`): returns the **empty list** when the
    ``run.transcript_dir`` field is missing, when the dir is **absent locally** (a
    run not shared/pulled), when it is not a directory, or when it holds **no
    matching files** — never raises. This empty list is what drives the viewer's
    "No transcripts for this run." state.
    """
    directory = transcript_dir_for(record, ledger_path)
    if directory is None:
        return []
    try:
        if not directory.is_dir():
            return []
        files = sorted(directory.glob(_TRANSCRIPT_GLOB), key=lambda p: p.name)
    except OSError:
        # A permission / FS error reading the dir is treated like an absent dir —
        # the viewer shows "no transcripts" rather than crashing.
        return []
    return [TranscriptEntry(label=path.stem, path=path) for path in files]


def read_transcript(path: Path) -> str:
    """Read one transcript file's text, or ``""`` when it can't be read.

    The read half of the pure layer: returns the file's UTF-8 text for the
    ``TranscriptScreen`` to scroll. Defensive (mirroring :func:`_dig`) — a
    **missing**, **unreadable**, or otherwise erroring file resolves to the
    **empty string**, never raising, so a transcript that vanished between listing
    and opening degrades to a blank view instead of a traceback.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ===========================================================================
# Transcript tokenizing (spec 038 — the pure, colour-free spine).
#
# A preserved game is one wall of plain text mixing content a reviewer reads in
# completely different ways: the skeleton marking where a Night ends and a Day
# begins, seven people talking in turn, each player's private thoughts, the
# Moderator's factual recaps, and an opening cast list. :func:`tokenize_transcript`
# splits that text into an ordered run of ``(text, kind)`` spans where ``kind``
# names a **semantic** role — never a colour. The UI (``TranscriptScreen``) owns
# the kind → style mapping; **this layer stays free of Rich and Textual**, which
# is precisely what lets the parsing — the part most worth testing — be tested as
# a pure function with no terminal (the same property that lets
# ``tests/test_ledger_model.py`` run headless).
#
# The invariant that makes it safe: ``"".join(text for text, _ in spans)`` is the
# input, byte for byte. Colour is applied to the **display** only, so a dropped or
# duplicated character would change what a reviewer reads while being invisible
# against a 30 KB wall of text. ``tests/test_transcript_highlight.py`` asserts it
# over every committed transcript.
# ===========================================================================

# The kinds recognised so far. Names are **semantic, never colours**
# (``speaker-mafia`` would be an acceptable later kind; ``speaker-red`` never is)
# and lowercase-hyphenated, so the UI and the tests can name them instead of
# repeating string literals.
KIND_MARKER = "marker"
KIND_PLAIN = "plain"

# Slice 2. ``attr`` is the **value** of a detail-carrying attribute inside a
# marker — ``Avery``, not ``name="Avery"`` and not the tag around it — so a
# reviewer's eye lands on *who* rather than on the punctuation holding the name
# (functional-spec §2: "the specifics are readable at a glance rather than buried
# in punctuation"). ``field-label`` is a cast-list field's ``Label:``, colon
# included, so an entry can be skimmed by field instead of read as a paragraph.
KIND_ATTR = "attr"
KIND_FIELD_LABEL = "field-label"

# Slice 3 — the three *kinds of content* a reviewer reads differently, now that
# the skeleton has receded (functional-spec §2). ``speaker`` is the ``Name:``
# prefix of a spoken line and ``speech`` the words after it; ``thought`` is the
# body of a ``<thought player="X">…</thought>`` private reflection, which must
# never be mistakable for something said aloud; ``recap`` is the body of a
# ``<recap>…</recap>`` moderator status line, which reads as fact rather than
# opinion and doubles as a scroll landmark.
#
# **Sides are not involved yet.** Slice 4 splits ``speaker``/``speech`` into
# side-bearing kinds read from the ``<setup>`` cast list; this slice only makes
# the three kinds of content distinguishable *from each other*.
KIND_SPEAKER = "speaker"
KIND_SPEECH = "speech"
KIND_THOUGHT = "thought"
KIND_RECAP = "recap"

# Slice 4 — the side-bearing kinds, the spec's headline requirement
# (functional-spec §2: "Each side has its own colour, applied to a speaker's name
# and to what they say"). Each is a *qualified* form of the Slice-3 kind above
# it, and the qualifier names the **side**, never the colour: ``speaker-mafia``
# is a kind, ``speaker-red`` never is. The UI picks the two hues.
#
# The neutral :data:`KIND_SPEAKER` / :data:`KIND_SPEECH` are **not** retired by
# these — they stay as the appearance of a line whose side is *unknown*, which
# is a pre-spec-022 game (no ``<player>`` tag to read a cast list from) or a name
# absent from the cast list. See :func:`_cast_side_map` for why that is the
# fallback rather than :data:`KIND_PLAIN`.
KIND_SPEAKER_MAFIA = "speaker-mafia"
KIND_SPEAKER_LAW_ABIDING = "speaker-law-abiding"
KIND_SPEECH_MAFIA = "speech-mafia"
KIND_SPEECH_LAW_ABIDING = "speech-law-abiding"

# ...and the side-bearing form of :data:`KIND_ATTR`, for the two attribute values
# whose side the transcript states rather than leaves to be guessed: the cast
# entry's ``role="…"`` (so "the side colours used in the cast list match the ones
# used for dialogue" — functional-spec §2) and the ``<thought player="…">``
# owner's name (so "the owner's name carries that player's side colour" — the
# same section, stated explicitly).
#
# Every OTHER ``attr`` value stays achromatic :data:`KIND_ATTR`, deliberately.
# From Slice 4 onward **colour means side**, and a bare ``name="Avery"`` or a
# vote's ``initiator="Avery"`` carries no side of its own — tinting it would
# either invent one or spend a hue that then means nothing. ``attr`` lifts by
# brightness and weight instead, which is what keeps the two side hues honest.
KIND_ATTR_MAFIA = "attr-mafia"
KIND_ATTR_LAW_ABIDING = "attr-law-abiding"

# Slice 5 — the reviewer's OWN SEAT, "marked more strongly than the rest of its
# side" (functional-spec §2). The seat is bold **within** its side colour, so it
# stands out from its side-mates rather than reading as a third side; each kind
# below is therefore its Slice-4 kind plus a weight, and the UI rule for it is
# the corresponding side rule's ``color:`` plus ``text-style: bold``. Semantic,
# not presentational: ``speaker-mafia-human`` names *whose seat it is*, exactly
# as ``speaker-mafia`` names which side they are on. ``speaker-bold`` never is.
#
# **Six kinds, because the two axes are independent.** A side comes from the
# cast list; a seat comes from the ``human="true"`` marker or, failing that,
# from the preamble's welcome line (:func:`_human_seat`) — and a game can know
# either one without the other. All 30 pre-spec-022 transcripts are exactly that
# case: they name the seat in their preamble and yield no cast map at all, so
# :data:`KIND_SPEAKER_HUMAN` / :data:`KIND_SPEECH_HUMAN` are "the seat whose side
# is unknown", the same way the neutral pair above is "a line whose side is
# unknown". Folding the two axes into one kind would have to discard one of two
# facts the file actually states.
#
# **``attr`` deliberately gains no human form.** The seat's own cast entry and
# its ``<thought player="…">`` owner name are already :data:`KIND_ATTR_MAFIA` /
# :data:`KIND_ATTR_LAW_ABIDING`, and Slice 4's styling task kept ``attr``'s bold
# on both — so bold-within-the-side would be invisible there. Functional-spec §2
# scopes the requirement to "that seat's **lines**" in any case, and bold on a
# player's *line* is unspent precisely so it can mean this one thing (Slice 3).
KIND_SPEAKER_HUMAN = "speaker-human"
KIND_SPEECH_HUMAN = "speech-human"
KIND_SPEAKER_MAFIA_HUMAN = "speaker-mafia-human"
KIND_SPEAKER_LAW_ABIDING_HUMAN = "speaker-law-abiding-human"
KIND_SPEECH_MAFIA_HUMAN = "speech-mafia-human"
KIND_SPEECH_LAW_ABIDING_HUMAN = "speech-law-abiding-human"

# Spec 039 — the twenty-first kind, and the first one added by a spec other than
# 038. ``diary`` is the **body** of a ``<diary player="X" day="N">…</diary>``
# element: the private note an AI writes at the Day->Night hinge, summing up the
# Day it has just lived through. The writer files it in the Day's *trailer*, so
# it renders between the last ``</round>`` and ``</day>`` — "between the day it
# was written about and the Night that followed" (functional spec 039 §2).
#
# **A kind of its own rather than reusing :data:`KIND_THOUGHT`.** The two are
# neighbours — both private, both never said aloud, both muted — but they answer
# different questions and a reviewer reads them differently: a thought is one
# player's reaction inside a single speaking round, a diary is that player's
# settled read of a whole Day, carried forward into the next one. Collapsing
# them would make the day trailer indistinguishable from the round bodies above
# it, which is the one thing its placement exists to say.
#
# **Never side-tinted**, exactly as :data:`KIND_THOUGHT` is not (tech-spec 039
# §2.8, which puts the diary body under ``thought``'s precedent in those words):
# a private reflection is not an act of allegiance, and the two hues mean *side*
# from spec 038's Slice 4 onward. The owner's name inside the tag carries the
# side instead (:func:`_attr_kind`), so whose diary it is still reads at a glance
# while the prose itself stays out of the side palette — which is also why this
# kind needs no new hue in a palette tech-spec 039 §2.8 records as fully spent.
KIND_DIARY = "diary"

# The canonical kind vocabulary, in the order the kinds were introduced — the
# single source of truth for which kinds exist, following the same house pattern
# as :data:`METRIC_ORDER` (a later spec appends its entries and nothing else has
# to change). The UI builds its kind → style map from this, and a kind absent
# from a style map must fall back to unstyled rather than raising.
#
# Slice 5 closed the vocabulary at twenty: the human seat's
# bold-within-its-side treatment is the last of spec 038's kinds. Spec 039
# appended the twenty-first, :data:`KIND_DIARY`, by exactly the route that house
# pattern promises — one constant, one entry here, one ``__all__`` line, one tag
# name in :data:`_MARKER_TAGS`, one entry in :data:`_INLINE_CONTENT_KINDS` and
# one branch in :func:`_attr_kind`. No existing kind, rule or span moved.
TRANSCRIPT_KINDS: tuple[str, ...] = (
    KIND_MARKER,
    KIND_PLAIN,
    KIND_ATTR,
    KIND_FIELD_LABEL,
    KIND_SPEAKER,
    KIND_SPEECH,
    KIND_THOUGHT,
    KIND_RECAP,
    KIND_SPEAKER_MAFIA,
    KIND_SPEAKER_LAW_ABIDING,
    KIND_SPEECH_MAFIA,
    KIND_SPEECH_LAW_ABIDING,
    KIND_ATTR_MAFIA,
    KIND_ATTR_LAW_ABIDING,
    KIND_SPEAKER_HUMAN,
    KIND_SPEECH_HUMAN,
    KIND_SPEAKER_MAFIA_HUMAN,
    KIND_SPEAKER_LAW_ABIDING_HUMAN,
    KIND_SPEECH_MAFIA_HUMAN,
    KIND_SPEECH_LAW_ABIDING_HUMAN,
    KIND_DIARY,
)

# The two sides, as the internal token that selects a side-bearing kind. Private
# on purpose: the module's *public* vocabulary is the kind list above, and a
# second exported spelling of "mafia" would be one more thing to keep in step.
#
# Hyphenated to match the kinds they build, not the game engine's own
# ``law_abiding`` role key — these tokens exist to be pasted onto a kind name.
_SIDE_MAFIA = "mafia"
_SIDE_LAW_ABIDING = "law-abiding"

# Side → kind, one table per base kind. Written as lookups rather than an
# f-string so the kind vocabulary stays a set of *literals* a reader can grep
# for, and so an unknown side can never silently manufacture a kind name that
# :data:`TRANSCRIPT_KINDS` does not contain.
#
# ``.get(side, <neutral>)`` with ``side=None`` is the unknown-side path in all
# three: no side map, or a name the cast list does not carry.
_SIDE_SPEAKER_KINDS: dict[str, str] = {
    _SIDE_MAFIA: KIND_SPEAKER_MAFIA,
    _SIDE_LAW_ABIDING: KIND_SPEAKER_LAW_ABIDING,
}
_SIDE_SPEECH_KINDS: dict[str, str] = {
    _SIDE_MAFIA: KIND_SPEECH_MAFIA,
    _SIDE_LAW_ABIDING: KIND_SPEECH_LAW_ABIDING,
}
_SIDE_ATTR_KINDS: dict[str, str] = {
    _SIDE_MAFIA: KIND_ATTR_MAFIA,
    _SIDE_LAW_ABIDING: KIND_ATTR_LAW_ABIDING,
}

# Dialogue kind → the same kind for the **reviewer's own seat** (Slice 5). Keyed
# by the kind rather than by the side, so the human axis composes with whatever
# the side axis already decided — including the neutral case, which is not a
# leftover but the whole of the pre-spec-022 story (a seat named in the preamble
# of a game with no cast list to read a side from).
#
# A lookup rather than ``kind + "-human"`` for the same reason
# :data:`_SIDE_SPEAKER_KINDS` is one: the vocabulary stays a set of **literals a
# reader can grep for**, and a kind with no entry here (``marker``, ``attr``,
# ``thought``, ``recap``, an unrecognised string) can never have a name
# manufactured for it that :data:`TRANSCRIPT_KINDS` does not contain. Every call
# site uses ``.get(kind, kind)``, so "no human form" means "unchanged".
_HUMAN_KINDS: dict[str, str] = {
    KIND_SPEAKER: KIND_SPEAKER_HUMAN,
    KIND_SPEECH: KIND_SPEECH_HUMAN,
    KIND_SPEAKER_MAFIA: KIND_SPEAKER_MAFIA_HUMAN,
    KIND_SPEAKER_LAW_ABIDING: KIND_SPEAKER_LAW_ABIDING_HUMAN,
    KIND_SPEECH_MAFIA: KIND_SPEECH_MAFIA_HUMAN,
    KIND_SPEECH_LAW_ABIDING: KIND_SPEECH_LAW_ABIDING_HUMAN,
}

# The role labels the writer emits, mapped to their side. Verbatim from
# ``eval_transcript._ROLE_LABELS`` (``{"mafia": "Mafia", "law_abiding":
# "Law-abiding Citizen"}``), which is the format's authority, and confirmed
# exhaustive over the corpus: all 1,960 ``role="…"`` values in the 268 spec-022
# files are one of these two (1,424 Law-abiding Citizen, 536 Mafia).
#
# A **whitelist**, exactly like :data:`_MARKER_TAGS`, :data:`_FIELD_LABELS` and
# :data:`_ATTR_NAMES`, and for the sharpest version of the same reason: the
# writer's own fallback is ``_ROLE_LABELS.get(role, role or "unknown role")``, so
# an unmapped role reaches the transcript as a raw string. Guessing a side from
# an unrecognised label is exactly the failure tech-spec §3 names — "a wrong map
# is actively misleading rather than merely ugly". An unrecognised label simply
# leaves that player out of the map, and their speech reads neutral.
_ROLE_SIDES: dict[str, str] = {
    "Mafia": _SIDE_MAFIA,
    "Law-abiding Citizen": _SIDE_LAW_ABIDING,
}

# The structural tag vocabulary ``graphia.tools.eval_transcript`` emits — the
# whitelist that decides whether a ``<…>`` line is skeleton or just text that
# happens to start with an angle bracket. A whitelist rather than "anything
# tag-shaped" is the *never guess* posture this module applies everywhere else:
# persona prose and player speech are model-generated, so an unrecognised
# ``<foo>`` line degrades to :data:`KIND_PLAIN` instead of being styled as
# scaffolding it is not.
#
# ``transcript`` / ``setup`` / ``preamble`` / ``night`` / ``day`` / ``round`` /
# ``endgame`` are the section delimiters (attribute-free, content-free — the
# whole line is one marker). ``kill`` is inline-with-content. ``player``,
# ``vote``, ``recap`` and ``thought`` are the four tags whose **innards** later
# slices reclassify — their opening tags are markers *now* so those slices only
# have to change an inner span's kind (Slice 3's "the surrounding tag stays
# ``marker``") or split attribute values out of a tag that is already a marker
# (Slice 2), rather than promote a whole line from ``plain``.
#
# ``diary`` is spec 039's addition and the only entry here not written by spec
# 038. It is inline-with-content and attribute-carrying, structurally identical
# to ``thought``, so it joins on two lines — this one and
# :data:`_INLINE_CONTENT_KINDS` — which is precisely the extension point Slice 1
# of spec 038 built the split for. Because the vocabulary is a **whitelist**,
# adding a name can only affect lines that carry it, and ``<diary`` occurs zero
# times across the 298 committed transcripts (measured): no committed line can
# tokenize differently for this entry existing.
_MARKER_TAGS: frozenset[str] = frozenset(
    {
        "transcript",
        "setup",
        "preamble",
        "night",
        "day",
        "round",
        "endgame",
        "kill",
        "player",
        "vote",
        "recap",
        "thought",
        "diary",
    }
)

# The kind given to the CONTENT of an inline ``<tag …>content</tag>`` element,
# by tag name — the extension point Slices 2–3 edit rather than rewrite. Slice 1
# split "the tag" from "what is inside it" precisely so that claiming a body is
# one entry here rather than a new branch: Slice 3 is a two-line change.
#
# ``kill`` maps to :data:`KIND_MARKER` because no later slice reclassifies a
# night kill's ``Name — Side`` payload: the tech-spec §2 A table lists ``<kill>``
# plainly among the markers, and with the adjacent-span coalescing below the
# whole ``<kill>Avery — Law-abiding Citizen</kill>`` line therefore reads as one
# marker span.
#
# ``thought`` and ``recap`` are Slice 3's two entries — the surrounding tag stays
# :data:`KIND_MARKER` on both, so only the body changes kind. ``diary`` is spec
# 039's, and it collected on that promise exactly: the tag name above and this
# line, with no new branch anywhere in the chain. Every other inline tag still
# defaults to :data:`KIND_PLAIN`, which is the right answer for the ``(no persona
# recorded)`` body of an inline ``<player>``: that is prose, not a kind of its
# own.
_INLINE_CONTENT_KINDS: dict[str, str] = {
    "kill": KIND_MARKER,
    "thought": KIND_THOUGHT,
    "recap": KIND_RECAP,
    "diary": KIND_DIARY,
}

# One transcript line's tag head: ``<name>``, ``</name>`` or ``<name attrs…>``.
# ``[^>]*`` for the attribute run is deliberate — the writer emits
# ``name="…" role="…"`` / ``initiator="…" target="…"`` / ``player="…"`` values
# that never contain ``>``; one that did would simply fail to match and the line
# would degrade to plain rather than being mis-split.
_TAG_HEAD_RE = re.compile(
    r"^<(?P<slash>/?)(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?P<attrs>\s[^>]*)?>"
)

# The detail-carrying attributes whose **values** become :data:`KIND_ATTR` spans
# (tech-spec §2 A). Five of them are spec 038's, verified across all 298
# committed transcripts — ``name``/``role`` on ``<player>`` (1960 each),
# ``player`` on ``<thought>`` (8183) and ``initiator``/``target`` on ``<vote>``
# (1005 each); that corpus contains no sixth attribute name.
#
# ``day`` is spec 039's sixth, carried by ``<diary player="…" day="…">``, and it
# earns its place by the same test the other five pass: it is *the* thing that
# tells one of a player's diaries from the next, so a reviewer asking "what did
# Ava conclude on Day 2" reads it rather than the punctuation holding it
# (tech-spec 039 §2.8 asks for it in those words — "the ``day`` attribute is an
# ``attr`` span"). **It cannot move a committed transcript**: ``day="`` occurs
# zero times across the 298 files (measured), and :data:`_ATTR_VALUE_RE`'s
# lookbehind stops it matching the tail of a hypothetical ``birthday="…"``.
#
# A whitelist, not "every ``x="…"`` pair", for the same *never guess* reason the
# tag vocabulary is one: an attribute is lifted out of the punctuation because a
# reviewer reads it as a **specific** — a person's name, a role, the Day an entry
# sums up. Spec 038's own Slice 5 duly added ``human="true"`` to the human seat's
# ``<player>`` tag and duly left it OUT of this tuple: a machine flag is not a
# detail worth picking out, so it stays part of the surrounding marker. The
# reader reads it through :data:`_HUMAN_ATTR_RE`, which splits no span. ``day``
# is the other side of that line — a value a person reads, not a flag a program
# does — which is why the two attributes spec 038 and 039 added land opposite
# ways round.
_ATTR_NAMES: tuple[str, ...] = (
    "name",
    "role",
    "player",
    "initiator",
    "target",
    "day",
)

# One ``name="value"`` pair inside a tag's attribute run. Only the ``value``
# group becomes an :data:`KIND_ATTR` span — the attribute name, the ``="`` and
# the closing quote stay :data:`KIND_MARKER`, which is the whole point of the
# split (the marker keeps the punctuation, the reviewer gets the specifics).
#
# ``[^"]*`` cannot run past the closing quote, and the lookbehind stops a
# hypothetical ``nickname="…"`` from being split at its ``name="`` tail. A
# malformed run that matches nothing simply yields the single marker span it
# would have been before Slice 2 — degradation, never a raise.
#
# The ``key`` group is Slice 4's only change here: two of the five values are
# side-bearing and the other three are not, so the splitter has to know *which*
# attribute it is looking at (see :func:`_attr_kind`), and the cast-list scan
# has to pair a ``name`` with its ``role`` (see :func:`_cast_side_map`).
_ATTR_VALUE_RE = re.compile(
    r'(?<![A-Za-z0-9_-])(?P<key>'
    + "|".join(_ATTR_NAMES)
    + r')="(?P<value>[^"]*)"'
)

# The single information line at the very top of a game, e.g.
# ``Game 1 | provider=ollama | large_model=qwen3-coder:30b | … | games=50``.
# Built by ``eval_transcript._header`` as ``" | ".join(parts)`` with
# ``f"Game {game_index}"`` always first and every other part omitted when the
# run metadata lacks it — hence the optional tail, which keeps a thin
# ``Game 1`` header recognised. Matched **only against the file's first line**
# (functional-spec §2 calls it "the single information line at the very top"), so
# a player who somehow says "Game 4 | ..." mid-transcript is never mistaken for a
# header.
_METADATA_LINE_RE = re.compile(r"^Game \d+(?: \| .+)?$")

# The bare speaking-round label written inside a ``<round>`` block, e.g.
# ``Round 3.`` — skeleton, not content, per functional-spec §2 ("the plain round
# markers inside a Day are treated as skeleton too"). Anchored end-to-end so an
# ordinary sentence merely *beginning* "Round 3. ..." is left as plain text.
_ROUND_LABEL_RE = re.compile(r"^Round \d+\.$")

# The cast list's field labels, verbatim from the writer's ``_persona_lines``
# (``graphia.tools.eval_transcript``) — that function is the format's authority,
# and these five ``f"Field: {value}"`` prefixes are every label it emits.
# ``Public legend:`` / ``True self (hidden):`` are the Mafioso's two layers;
# ``Persona:`` is the single honest one a Citizen gets.
#
# Not labels, deliberately: ``(no persona recorded)`` and ``(persona has no
# recorded detail)`` are parenthesised notes rather than fields, and
# ``Pointing round N:`` belongs to a Night's pick list rather than the cast list.
_FIELD_LABELS: tuple[str, ...] = (
    "Personality:",
    "Manner:",
    "Public legend:",
    "True self (hidden):",
    "Persona:",
)

# A line **beginning** with one of the labels. Longest-first so ``Persona:``
# cannot shadow ``Personality:`` whatever order :data:`_FIELD_LABELS` is written
# in, and matched against the *lstripped* body so the pre-spec-022 era's
# four-space-indented persona lines are recognised by the same single rule as
# spec-022's flush-left ones (the indent is already its own plain span).
#
# The match covers the label **including its colon**; the prose after it is
# content and stays plain.
_FIELD_LABEL_RE = re.compile(
    "|".join(
        re.escape(label)
        for label in sorted(_FIELD_LABELS, key=len, reverse=True)
    )
)

# A spoken line's ``Name:`` prefix (spec 038, Slice 3). The writer emits player
# speech as ``f"{speaker}: {text}"`` (``eval_transcript._append_messages``) —
# a bare display name, a colon, a space — and that is the whole shape available,
# because a Day round carries no other structure a reader could key off.
#
# **How permissive, and why exactly this permissive.** The name is one
# whitespace-free token that begins with a *letter* (``[^\W\d_]`` — Unicode, so
# ``Sofía`` and ``Inés`` match; a digit, a bullet, an opening parenthesis or a
# ``<`` does not) and contains no colon of its own. The colon must be followed by
# a space, so ``http://x`` and a bare ``Name:`` with nothing after it are not
# speech. Measured across all 298 committed transcripts this captures **22,508**
# lines under **498** distinct names and **not one** name that is absent from its
# own file's cast list — zero false positives on real data.
#
# Deliberately *not* driven by the parsed cast list, even though Slice 4 builds
# one: the 30 pre-spec-022 transcripts have no ``<player>`` tag at all, and
# tech-spec §2 B fixes their degradation as "markers, **speaker prefixes** and
# field labels still work, only the side colour is missing". A cast-list-gated
# rule would silently drop every speaker prefix in those 30 files. Sides are a
# *judgement* about a name and must never be guessed; "this line is somebody
# speaking" is a *shape*, and the shape is unambiguous.
#
# The deliberate misses are all in the same direction — an unrecognised line
# falls back to ``plain``, which the spec's degradation posture prefers to a
# wrong capture: a name carrying an internal space (``Mary Ann: hi``), a
# continuation line of a multi-line utterance, and a lowercase-*written*
# structural label are all left as plain text.
_SPEAKER_RE = re.compile(r"^(?P<name>[^\W\d_][^\s:]*):(?= )")

# Line prefixes that are shaped exactly like a speaker but are the **writer's own
# vocabulary**, not a player — the same whitelist-the-writer's-shapes posture as
# :data:`_FIELD_LABELS` and :data:`_MARKER_TAGS`, rather than a heuristic.
#
# - ``Moderator`` is the moderator's public voice (``_append_messages`` emits
#   ``Moderator: <text>``); 2,655 lines in the corpus. Its private form,
#   ``Moderator (private to Avery): …``, needs no entry — the space before the
#   colon already fails :data:`_SPEAKER_RE`. Both stay :data:`KIND_PLAIN`, which
#   is this slice's deliberate answer for the moderator: he is neither a player
#   speaking nor a status recap, and inventing a kind for him is Slice 3 scope
#   this task does not have.
# - ``tally`` and ``outcome`` are the two field lines a ``<vote>`` block ends
#   with (``_VoteAssembler._render`` writes ``tally: 3 Yes, 3 No`` and
#   ``outcome: failed — …``); 1,005 of each.
#
# An earlier draft excluded these by requiring an initial capital instead. That
# was rejected on measurement: 39 of the corpus's cast names are lowercase
# (``mina``, ``arthur``, ``kai``, ``zara``, …) and a casing rule loses 426 real
# speaker lines to save three literals. A player genuinely named "tally" would be
# missed; that is a plain-text fallback on a name no roster has produced.
_NON_SPEAKER_PREFIXES: frozenset[str] = frozenset(
    {"Moderator", "tally", "outcome"}
)

# The tag that opens and closes the cast list. :func:`_cast_side_map` reads
# ``<player …>`` entries **only** between these two, so a ``<player …>``-shaped
# line anywhere else — model-generated speech, a persona's prose, a future
# format that reuses the tag — can never poison the side map. Compared against
# the lstripped line so the pre-spec-022 era's indented ``  <setup>`` is
# recognised by the same two literals.
_SETUP_OPEN = "<setup>"
_SETUP_CLOSE = "</setup>"

# The one tag the cast list is built from, and the two attributes a cast entry
# must carry for its player to have a *known* side.
_CAST_TAG = "player"
_CAST_NAME_ATTR = "name"
_CAST_ROLE_ATTR = "role"

# The ``<thought player="X">`` tag and its owner attribute — the FIRST place a
# *name* attribute is side-bearing, because functional-spec §2 requires it in
# those words ("the owner's name carries that player's side colour"). Spec 039
# added the second, below.
_THOUGHT_TAG = "thought"
_THOUGHT_OWNER_ATTR = "player"

# The ``<diary player="X" day="N">`` tag and its owner attribute (spec 039). The
# attribute is spelled the same as ``<thought>``'s and is side-bearing for the
# same reason, but the pair is written out separately rather than reusing the
# thought constants: :func:`_attr_kind` keys on the ``(tag, key)`` PAIR, and
# folding the two together would hide the fact that this is a second,
# independent decision about a second tag — the thing the tag-scoped rule exists
# to keep explicit. A future ``<foo player="…">`` gets no side by default, which
# is the posture the whole module is built on.
#
# ``day`` needs no constant here: it is not side-bearing and never will be. It
# names a Day, not a person, and colour has meant *side* since spec 038's Slice
# 4 — so it lifts out of the punctuation as a plain :data:`KIND_ATTR` through
# :data:`_ATTR_NAMES` and :func:`_attr_kind`'s default, with no rule of its own.
_DIARY_TAG = "diary"
_DIARY_OWNER_ATTR = "player"

# The scene-setting section, and the two literals that delimit it. The welcome
# line :func:`_human_seat` falls back to is scoped to it for exactly the reason
# the cast scan is scoped to ``<setup>``: every word of a transcript outside
# these sections is model-generated, and a player who types "Moderator: A new
# game begins. Welcome, Bo." into a Day speech must not re-seat the reviewer.
# Compared against the lstripped line, so the pre-spec-022 era's indented
# ``  <preamble>`` is recognised by the same two literals.
_PREAMBLE_OPEN = "<preamble>"
_PREAMBLE_CLOSE = "</preamble>"

# The human seat's marker, written onto its ``<player>`` tag by
# ``graphia.tools.eval_transcript._render_setup`` (spec 038, Slice 5) and read
# back here. **Deliberately absent from :data:`_ATTR_NAMES`** — ratified in
# Slice 2 — because a machine flag is not a "specific a reviewer reads": it is
# never lifted into its own :data:`KIND_ATTR` span and stays inside the
# surrounding marker, so the tag splits as ``<player name="`` · ``Avery`` ·
# ``" role="`` · ``Law-abiding Citizen`` · ``" human="true">``. This pattern
# exists for the seat scan alone and never for span-splitting; its lookbehind
# mirrors :data:`_ATTR_VALUE_RE`'s, so a hypothetical ``nonhuman="true"`` is not
# read as the flag.
_HUMAN_ATTR_RE = re.compile(r'(?<![A-Za-z0-9_-])human="(?P<value>[^"]*)"')

# The one value that marks the seat — a whitelist of a single literal, the same
# never-guess posture as :data:`_ROLE_SIDES`. The writer emits the attribute
# **only** on the human's entry and only with this value, so ``human="false"``,
# ``human=""`` or a future tri-state marks nothing rather than being coerced
# into a boolean.
_HUMAN_ATTR_TRUE = "true"

# The moderator's opening greeting, which names the person's seat in every one
# of the 298 committed transcripts (measured: exactly once each, always inside
# ``<preamble>``, and always in agreement with the ``(no persona recorded)``
# seat where the cast list is parseable). ``graphia.nodes.setup.collect_name``
# emits it as ``f"A new game begins. Welcome, {name}."`` and the writer prefixes
# the moderator's voice, so the whole line is anchored end to end and the name
# is everything between the comma and the final full stop — greedy, so a seat
# called ``Dr. Aziz`` keeps its own period.
#
# A **heuristic behind an explicit marker**, per tech-spec §3, not the primary
# route: it depends on the moderator's wording, and every newly written
# transcript carries the marker instead. A file whose greeting is worded
# differently simply yields no seat, and no seat means no bold.
_WELCOME_RE = re.compile(
    r"^Moderator: A new game begins\. Welcome, (?P<name>.+)\.$"
)


def _cast_side_map(lines: list[str]) -> dict[str, str]:
    """Read the ``<setup>`` cast list into ``name → side`` (spec 038, Slice 4).

    **The tokenizer's only state, and the one thing in it that is not per-line.**
    Every rule before this slice was a judgement about a single line in
    isolation; a side is not — ``Avery: I saw nothing`` says nothing at all about
    which side Avery is on, and the answer is 300 lines above it in the cast
    list. So :func:`tokenize_transcript` scans the whole text once for this map
    and threads it, read-only, into the per-line chain. The chain itself stays
    pure and stateless: :func:`_line_spans` is still a function of its arguments
    with no memory between calls, it just takes one argument more.

    **Scoped to ``<setup>``, deliberately.** ``<player …>`` occurs nowhere else
    in any of the 298 committed transcripts, so scoping and not scoping are
    indistinguishable on today's data — but every word of a transcript outside
    the cast list is model-generated, and an unscoped scan would let a player who
    typed ``<player name="Bo" role="Mafia">`` into a Day speech rewrite the
    colour of the real Bo's every line. The scan is already walking the file, so
    the guard costs three lines.

    **The spec-022 form only** (tech-spec §2 B, the author's explicit decision).
    The 30 pre-spec-022 transcripts write their cast list as an indented
    ``Name — Role`` and carry no ``<player>`` tag at all, so they yield ``{}``
    here and every speaker in them keeps the neutral :data:`KIND_SPEAKER` /
    :data:`KIND_SPEECH`. That is best-effort degradation, not a gap to be closed:
    a **second parser for the old form was considered and declined**, and this
    same silence is the forward-compatibility posture for the next format change.

    **Never a guess, in three places:**

    - an entry whose ``role`` is not one of :data:`_ROLE_SIDES`' two labels
      contributes no side;
    - a name the cast list gives **two different sides** (or one side and one
      unrecognised role) is dropped entirely rather than resolved first-wins —
      an ambiguous name has no known side, and a coin-flip here would paint a
      whole game's dialogue in the wrong colour;
    - a name absent from the result is left **neutral**, never guessed and never
      demoted to :data:`KIND_PLAIN` (see :func:`_line_spans`).

    Returns a plain ``dict`` — a value, not a cache. Two calls on the same text
    return equal maps and the function reads nothing outside its argument.
    """
    seen: dict[str, set[str | None]] = {}
    in_setup = False
    for line in lines:
        body = line.strip()
        if body == _SETUP_OPEN:
            in_setup = True
            continue
        if body == _SETUP_CLOSE:
            in_setup = False
            continue
        if not in_setup:
            continue
        match = _TAG_HEAD_RE.match(body)
        if (
            match is None
            or match.group("slash")
            or match.group("name") != _CAST_TAG
        ):
            continue
        attrs = match.group("attrs")
        if not attrs:
            continue
        values = {
            pair.group("key"): pair.group("value")
            for pair in _ATTR_VALUE_RE.finditer(attrs)
        }
        name = values.get(_CAST_NAME_ATTR)
        if not name:
            continue
        seen.setdefault(name, set()).add(
            _ROLE_SIDES.get(values.get(_CAST_ROLE_ATTR, ""))
        )
    # One unambiguous, recognised side or nothing: a name whose entries disagree,
    # or whose only role label was unrecognised, is simply absent from the map.
    return {
        name: next(iter(sides))
        for name, sides in seen.items()
        if len(sides) == 1 and None not in sides
    }


def _marked_seat_name(body: str) -> str | None:
    """The ``name`` of a ``<player … human="true">`` cast entry, else ``None``.

    One line in, one name out. The caller (:func:`_human_seat`) owns the
    ``<setup>`` scoping and the ambiguity rule; this only answers "does this
    line mark the seat, and whose is it".

    Both halves are read straight off the tag's attribute run: the flag through
    :data:`_HUMAN_ATTR_RE` (which is *not* in :data:`_ATTR_NAMES`, so it never
    becomes a span of its own), the name through :data:`_ATTR_VALUE_RE` (which
    is, so it does). An entry carrying the flag but no usable ``name`` marks
    nothing — there would be no speaker prefix to match it against.
    """
    match = _TAG_HEAD_RE.match(body)
    if (
        match is None
        or match.group("slash")
        or match.group("name") != _CAST_TAG
    ):
        return None
    attrs = match.group("attrs")
    if not attrs:
        return None
    flag = _HUMAN_ATTR_RE.search(attrs)
    if flag is None or flag.group("value") != _HUMAN_ATTR_TRUE:
        return None
    for pair in _ATTR_VALUE_RE.finditer(attrs):
        if pair.group("key") == _CAST_NAME_ATTR and pair.group("value"):
            return pair.group("value")
    return None


def _human_seat(lines: list[str]) -> str | None:
    """The reviewer's own seat, by name — or ``None`` when the game does not say.

    The tokenizer's **second** whole-file scan (spec 038, Slice 5), sitting
    beside :func:`_cast_side_map` and answering an independent question: that one
    asks *which side is this name on*, this one asks *which seat was the
    person's*. Both are facts a line cannot carry on its own, both are read once
    and threaded read-only into the per-line chain, and neither caches anything.

    "The person's seat" means **the seat**, not a live person: in a measured run
    it is played by the scripted stand-in (spec 026) and it is marked the same
    way, because ``PlayerState.is_human`` is what the engine sets on it either
    way (``graphia.nodes.setup.collect_name``) and it holds the same position in
    the game. Functional-spec §2 says so in as many words.

    **Two routes, and the marker wins:**

    1. the writer's ``<player … human="true">`` entry inside ``<setup>`` — the
       explicit statement, carried by every transcript written from Slice 5
       onward, and scoped to the cast list exactly as :func:`_cast_side_map` is
       (a ``<player … human="true">`` typed into a Day speech must not re-seat
       the reviewer);
    2. failing that, the preamble's ``Moderator: A new game begins. Welcome,
       <name>.`` line — the inference that covers the **298 already-committed
       transcripts**, none of which carries the marker. Measured across all of
       them: present exactly once, always inside ``<preamble>``, and never in
       disagreement with the seat the cast list leaves persona-less.

    The precedence is checked as "did the marker route see *anything*", not as
    "did it produce an answer". A file carrying two contradicting ``human="true"``
    entries has stated something and stated it incoherently; falling through to
    the greeting would be resolving a contradiction the file itself could not,
    which is the one thing this module never does. So a contradictory marker
    yields **no seat**, and no seat means no bold — the same shape as
    :func:`_cast_side_map` dropping a name whose cast entries disagree.

    **Never a guess, and never the persona side effect.** ``(no persona
    recorded)`` appears in all 298 files and identifies the same seat, and it is
    still not used: only AI players get personas, so that string encodes *"no
    persona"* rather than *"the person"* — a reliable correlation, not a
    statement, and the day a persona generator fails for an AI seat it would
    quietly mark the wrong one. A seat identifiable by neither of the two real
    routes is simply not bolded (:func:`_line_spans`).

    Returns a plain ``str`` or ``None`` — a value, not a cache. Reads nothing
    outside its argument and never raises.
    """
    marked: set[str] = set()
    welcomed: set[str] = set()
    section: str | None = None
    for line in lines:
        body = line.strip()
        if body in (_SETUP_OPEN, _PREAMBLE_OPEN):
            section = body
            continue
        if body in (_SETUP_CLOSE, _PREAMBLE_CLOSE):
            section = None
            continue
        if section == _SETUP_OPEN:
            name = _marked_seat_name(body)
            if name is not None:
                marked.add(name)
        elif section == _PREAMBLE_OPEN:
            welcome = _WELCOME_RE.match(body)
            if welcome is not None:
                welcomed.add(welcome.group("name"))
    candidates = marked if marked else welcomed
    return next(iter(candidates)) if len(candidates) == 1 else None


def _attr_kind(tag: str, key: str, value: str, sides: Mapping[str, str]) -> str:
    """The kind for one attribute value: :data:`KIND_ATTR`, or its side-bearing form.

    **The decision is per ``(tag, key)`` PAIR, never per attribute name** — which
    is why spec 039's ``<diary player="…">`` needed a branch of its own even
    though ``<thought player="…">`` already had one. Three pairs carry a side;
    every other pair does not, and a tag nobody has ruled on gets no side at all:

    - a cast entry's ``role="Mafia"`` — "the side colours used in the cast list
      match the ones used for dialogue". Its side comes from the label written
      right there, not from the map: the tag *states* the role, so no lookup and
      no ``<setup>`` scoping are involved. A cast list that contradicts itself
      therefore still shows each entry as the entry itself reads, while the
      contradicting *name* drops out of the map (:func:`_cast_side_map`).
    - a ``<thought player="Avery">`` owner's name — "the owner's name carries
      that player's side colour". This one *is* a map lookup, because the tag
      names a person and only the cast list knows their side.
    - a ``<diary player="Avery" day="2">`` owner's name (spec 039) — the same
      map lookup, for the same reason and by the same rule stated below: a diary
      tag names a person and a Day and no role, so the *name* is the only token
      on it that can tell a reviewer the side. Tech-spec 039 §2.8 puts the diary
      **body** under ``<thought>``'s precedent (muted, never side-tinted); its
      owner follows the other half of that same precedent, so ``Avery`` reads in
      Avery's colour whether the private note is one round's reaction or a whole
      Day's settled read. Leaving it achromatic was the alternative and was
      rejected: it would show one player's name in two different treatments a
      few lines apart in the same file, inviting a reviewer to read a
      distinction that is not there.

    The other four (a cast entry's ``name``, a vote's ``initiator`` and
    ``target``, and a diary's ``day``) stay achromatic :data:`KIND_ATTR`. Colour
    has meant side since Slice 4; those four are *specifics* a reviewer reads —
    three names and a Day number — lifted out of the punctuation by weight and
    brightness instead. Tinting the three names was considered and rejected on
    two grounds: it would leave ``attr`` with almost no occupants in a real
    transcript (before spec 039 it would have left it with none at all — those
    three were the whole achromatic half of the whitelist), and a vote marker's
    job is to say *who called it and against whom*, which the side colours on
    the ballot lines below it already answer. ``day`` is not even a candidate:
    it names no person, so there is no side it could carry.

    **The rule underneath all three halves:** inside a marker, the side colour
    lands on whichever token actually *tells the reviewer the side* — the role
    where a role is written (the cast entry), the name where none is (the
    thought and diary tags, which name a person and, for a diary, a Day).
    Everywhere a name sits next to its own role, the role carries the colour and
    the name does not, so the two treatments never compete on one line.
    """
    if tag == _CAST_TAG and key == _CAST_ROLE_ATTR:
        side = _ROLE_SIDES.get(value)
    elif tag == _THOUGHT_TAG and key == _THOUGHT_OWNER_ATTR:
        side = sides.get(value)
    elif tag == _DIARY_TAG and key == _DIARY_OWNER_ATTR:
        # Spec 039, the same rule one tag over. An unknown name — ``sides.get``
        # returning ``None`` because the cast list does not carry it, or because
        # this is one of the 30 pre-spec-022 games that yield no cast map at all
        # — falls back to the achromatic :data:`KIND_ATTR` exactly as a thought's
        # owner does. Never a guessed side; the degradation is the same one the
        # whole module is built around.
        side = sides.get(value)
    else:
        return KIND_ATTR
    return KIND_ATTR if side is None else _SIDE_ATTR_KINDS[side]


def tokenize_transcript(text: str) -> list[tuple[str, str]]:
    """Split a transcript into ordered ``(text, kind)`` spans (spec 038, §2 A).

    Pure: no I/O, no global state, no Rich/Textual import. ``text`` in, spans
    out, and **the spans always concatenate back to ``text`` exactly** ::

        "".join(text for text, _ in spans) == text

    That round-trip is the spine of spec 038 — it is what makes functional-spec
    §2's *"the stored game is never altered"* structurally true rather than
    merely intended — and it is asserted over every committed transcript by
    ``tests/test_transcript_highlight.py``.

    **Pure, but no longer per-line.** Slice 4 is where the tokenizer acquired its
    state: a side is not a property of the line it appears on, so the text is
    scanned first for the facts that describe the whole file and only then split
    into spans, with those facts threaded through as read-only arguments. Slice 5
    added the second such fact. The first pass reads

    - the ``<setup>`` cast list into a ``name → side`` map
      (:func:`_cast_side_map`), and
    - the reviewer's own seat, from the ``human="true"`` marker or, failing that,
      the preamble's welcome line (:func:`_human_seat`).

    Nothing is cached, nothing is global, and :func:`_line_spans` is still a pure
    function of its arguments. The property that genuinely changed in Slice 4 and
    still holds: **the same line can tokenize differently in two different
    files** — so a synthetic test that wants a side kind must include the
    ``<setup>`` block that grants it, and one that wants a bolded seat must carry
    the marker or the welcome line that names it.

    **Kinds recognised so far** (twenty-one — spec 038's twenty plus spec 039's
    :data:`KIND_DIARY`; see :data:`TRANSCRIPT_KINDS`):

    - :data:`KIND_MARKER` — the game's skeleton: the structural tags
      (``<transcript>``, ``<setup>``, ``<preamble>``, ``<night>``, ``<day>``,
      ``<round>``, ``<endgame>``, ``<kill>``, ``<player …>``, ``<vote …>``,
      ``<recap>``, ``<thought …>``, ``<diary …>``) and each one's closing form,
      the top ``Game N | provider=…`` metadata line, and the bare ``Round N.``
      label —
      **including the punctuation of a tag's attributes**, which stays marker so
      that only the values below are lifted out of it.
    - :data:`KIND_ATTR` — the **value** of a detail-carrying attribute inside a
      marker: the ``name``/``role`` of a cast entry, a thought's ``player``, a
      vote's ``initiator``/``target``, a diary's ``day`` (see
      :data:`_ATTR_NAMES`). ``Avery``, never ``name="Avery"``. Achromatic: it
      lifts by weight and brightness, because from Slice 4 onward colour means
      **side** and neither a bare name nor a Day number carries one.
    - :data:`KIND_ATTR_MAFIA` / :data:`KIND_ATTR_LAW_ABIDING` — the exceptions to
      that, the first two required by functional-spec §2 in so many words: a cast
      entry's ``role="…"`` (so the cast list and the dialogue agree on the
      colours), a ``<thought player="…">`` owner's name (so a private reflection
      shows whose it is in their own colour) and, from spec 039, a ``<diary
      player="…">`` owner's name on the same reasoning. See :func:`_attr_kind`,
      which decides per ``(tag, attribute)`` pair rather than per attribute
      name.
    - :data:`KIND_FIELD_LABEL` — a cast-list field's label, **colon included**
      (see :data:`_FIELD_LABELS`); the prose after it is content, not label.
    - :data:`KIND_SPEAKER` / :data:`KIND_SPEECH` — a spoken line **whose side is
      not known**, split at its colon: ``Avery:`` is the speaker,
      ``" I saw nothing."`` (leading space included, exactly as a field label's
      prose keeps its own) is the speech. See :data:`_SPEAKER_RE` for how
      permissive the name is and :data:`_NON_SPEAKER_PREFIXES` for the three
      writer-emitted prefixes that are shaped like a speaker and are not one.
    - :data:`KIND_SPEAKER_MAFIA` / :data:`KIND_SPEAKER_LAW_ABIDING` and
      :data:`KIND_SPEECH_MAFIA` / :data:`KIND_SPEECH_LAW_ABIDING` — the same two
      spans when the cast list *does* name the speaker's side. This is the
      spec's headline requirement: a speaker's name and their words both carry
      their side's colour, in every round and in a vote block alike.
    - :data:`KIND_SPEAKER_HUMAN` / :data:`KIND_SPEECH_HUMAN` and the four
      side-qualified forms (:data:`KIND_SPEAKER_MAFIA_HUMAN`,
      :data:`KIND_SPEAKER_LAW_ABIDING_HUMAN`, :data:`KIND_SPEECH_MAFIA_HUMAN`,
      :data:`KIND_SPEECH_LAW_ABIDING_HUMAN`) — the **reviewer's own seat**, whose
      lines are the same kind with a weight added. Bold lands *within* the side
      colour and never as a colour of its own, so the seat reads as more strongly
      marked than its side-mates rather than as a third side. Side and seat are
      independent facts and both, either or neither may be known; see
      :data:`_HUMAN_KINDS` and :func:`_human_seat`.
    - :data:`KIND_THOUGHT` — the **body** of a ``<thought player="X">…</thought>``
      private reflection; the tag around it stays :data:`KIND_MARKER`, and the
      owner's name inside that tag takes the owner's **side** (above). The body
      never does: a private reflection is not an act of allegiance, and giving it
      the speech colour would undo the one thing that makes it unmistakably
      private.
    - :data:`KIND_RECAP` — the **body** of a ``<recap>…</recap>`` moderator
      status line; again the tags around it stay :data:`KIND_MARKER`. Never
      side-tinted, for the plainest of reasons: the moderator has no side.
    - :data:`KIND_DIARY` — the **body** of a ``<diary player="X" day="N">…
      </diary>`` before-Night entry (spec 039), which the writer files in the
      Day's trailer so it renders between the last ``</round>`` and ``</day>``.
      Same shape as a thought — tags stay :data:`KIND_MARKER`, the owner's name
      takes the owner's side, the body never does — and a kind of its own
      because a Day's settled read is not a round's reaction, and the trailer
      would otherwise be indistinguishable from the round bodies above it.
    - :data:`KIND_PLAIN` — **everything else**, including every line separator.
      This fallback is what makes degradation total: an unrecognised tag, a
      pre-spec-022 transcript's indented ``Name — Role`` cast list, model-generated
      prose containing an angle bracket, or a future format change all come back
      as plain text instead of raising. **This function never raises on any
      input.**

    Guarantees beyond the round trip, all asserted by the corpus sweep:

    - **no span has empty text** — an empty span concatenates to nothing, so it
      would round-trip perfectly while handing the UI junk to style;
    - **every kind is a non-empty ``str``** drawn from :data:`TRANSCRIPT_KINDS`;
    - **no two adjacent spans share a kind** — adjacent same-kind runs are
      coalesced (see :func:`_coalesce_spans`), so a wall of unremarkable text
      arrives as one span rather than one per line.

    How a line is split (the extension point is :func:`_line_spans`):

    - a tag on a line of its own becomes one marker span — or, when it carries
      one of the five detail attributes, an alternating marker/attr/marker run
      whose pieces still concatenate to the tag exactly (:func:`_tag_head_spans`);
    - an inline ``<tag …>content</tag>`` becomes *three* spans — opening tag,
      content, closing tag — so a slice claiming a body need only change the
      content span's kind (or split attribute values out of the tag) instead of
      breaking one span into several. ``<thought>``, ``<recap>`` and spec 039's
      ``<diary>`` bodies are claimed that way
      (:data:`_INLINE_CONTENT_KINDS`); ``<kill>`` is the
      exception whose content is *already* marker, so it coalesces back to a
      single span. An element whose content does **not** close on the same line
      keeps that remainder :data:`KIND_PLAIN` rather than inheriting the tag's
      content kind — the extent is unknown, and styling an unknown extent as a
      private thought is exactly the kind of guess this module declines;
    - a cast-list ``Personality: …`` line splits into its label and the prose
      after it, in **both** transcript eras — spec-022 writes the label
      flush-left and the pre-022 runs indent it four spaces, and because the
      indent is already its own span one rule serves both;
    - a spoken ``Avery: I saw nothing.`` line splits into speaker and speech at
      the same boundary, **below** the field-label branch so that
      ``Personality: brisk`` is read as a label and not as a player named
      "Personality" (see :func:`_line_spans`);
    - a line's **leading indentation** is plain, never part of the marker span,
      so the three pre-spec-022 run dirs (which indent their ``  <round>`` tags
      and ``  Round N.`` labels) tokenize identically to the flush-left form;
    - line separators are plain spans of their own, so no styled span ever
      carries a ``\\n`` — a marker style can therefore never bleed to the end of
      a terminal row.

    **A ballot is speech** (spec 038, Slice 3 — the decision this docstring is
    required to record). Spec 022 strips the ``Moderator:`` voice off each vote,
    so a ``<vote>`` block's ``Avery: Yes`` line is shaped exactly like a spoken
    line and 5,656 of them sit in the corpus. They are kinded
    :data:`KIND_SPEAKER` + :data:`KIND_SPEECH` like any other utterance, **not**
    given a kind of their own, for three reasons:

    - it *is* the player's own word, in their own voice — the writer removed the
      moderator precisely so it would read that way;
    - functional-spec §2 asks that a speaker's name and words carry their side's
      colour "wherever they appear", and Slice 4 therefore makes a vote block
      show at a glance that the two Mafiosos both voted No. A separate
      achromatic ``ballot`` kind would throw that away — the single most useful
      thing colour can say about a vote;
    - telling a ballot from ordinary speech needs to know the line sits inside a
      ``<vote>`` block, and this tokenizer is deliberately per-line and
      stateless (the same reason :data:`_FIELD_LABELS` are matched unscoped).
      Keying off the payload being literally ``Yes``/``No`` instead would
      misfire the day a player answers a question with "Yes".

    **An unknown side falls back to the NEUTRAL speaker/speech, not to plain**
    (spec 038, Slice 4 — the decision this docstring is required to record,
    because the task text and tech-spec §2 A both say the words "yields
    ``plain``" and tech-spec §2 B contradicts them in the same document).

    Tech-spec §2 B fixes the degraded case as "a pre-022 file still gets
    ``marker``, **``speaker``**, **``speech``**, ``field-label`` and ``plain``
    spans — it simply has no side map", and Slice 4's own task text says the same
    thing in one breath ("speech falls back to ``plain`` **while markers, speaker
    prefixes and field labels still work**") — which is only coherent if "plain"
    there means *plain-looking*, not the ``plain`` kind. It is loose wording
    inherited from before ``speaker``/``speech`` existed as kinds at all: when
    tech-spec §2 A was written, the words after a name had no kind of their own,
    so "plain speech" was the only way to say "uncoloured".

    Reading it as the literal ``plain`` kind would *undo* Slice 3 for the 30
    pre-spec-022 games and for every unknown name — dropping the speaker prefix
    the same sentence promises to keep. So:

    - **"never guess" forbids assigning a SIDE**, not styling a line as speech.
      Which side someone is on is a judgement only the cast list can settle;
      *that somebody is speaking* is a shape the line proves on its own
      (see :data:`_SPEAKER_RE`, and the measured zero false positives behind it).
    - The neutral kinds are the body colour and nothing else — measured as a
      visual no-op against unstyled text — so "never guess a side" and "the
      neutral is the body colour" are the same rule, and the degraded view is
      exactly the uncoloured one tech-spec §3 asks for.

    **The reviewer's own seat is bold within its side** (spec 038, Slice 5 —
    functional-spec §2's "marked more strongly than the rest of its side"). The
    seat is read once per file by :func:`_human_seat`: the writer's
    ``<player … human="true">`` marker first, the preamble's ``Moderator: A new
    game begins. Welcome, <name>.`` line as the fallback covering the 298
    transcripts written before the marker existed, and **the marker wins when
    both are present**. Every line that seat speaks — Day speech and ``<vote>``
    ballot alike, since spec 022 strips the moderator's voice off a ballot
    precisely so it reads as the player's own word — takes the human form of
    whichever kind the side axis chose.

    The two axes compose in all four combinations: side and seat both known (the
    268 spec-022 games), seat known and side not (all 30 pre-spec-022 games,
    which name the seat in their preamble and yield no cast map), side known and
    seat not (a file whose greeting is worded differently), and neither. **A seat
    identifiable by neither route is simply not bolded** — the same never-guess
    rule the side map follows, and the reason ``(no persona recorded)`` is *not*
    consulted even though it identifies the same seat in all 298 files: it is a
    side effect of only AI players getting personas, and encodes "no persona"
    rather than "the person".

    Nothing outside a spoken line takes a human form. The seat's cast entry and
    its ``<thought player="…">`` owner name stay :data:`KIND_ATTR_MAFIA` /
    :data:`KIND_ATTR_LAW_ABIDING`, which already carry weight; the marker itself
    is not even an :data:`KIND_ATTR` span (``human`` is absent from
    :data:`_ATTR_NAMES` by ratified decision), so it stays inside the surrounding
    marker punctuation where a machine flag belongs.

    **Pre-spec-022 games degrade by that same single rule** (tech-spec §2 B,
    best-effort by the author's explicit decision). Those 30 files write their
    cast list as an indented ``Name — Role`` and carry no ``<player>`` tag, so
    :func:`_cast_side_map` returns ``{}`` for them and **no second parser exists
    for the old form** — that was considered and declined. Everything else in
    those files still works: markers, speaker prefixes, speech, field labels,
    thought and recap bodies. Only the two hues are missing. A future format
    change will degrade in exactly the same way rather than raising.
    """
    lines = text.split("\n")
    # PASS 1 — the two whole-file facts a line cannot carry on its own, read once
    # each and threaded read-only into the per-line chain. The cast list gives
    # every name its side (Slice 4, :func:`_cast_side_map`); the ``human="true"``
    # marker — or, for the 298 files written before it existed, the preamble's
    # welcome line — gives the reviewer's own seat (Slice 5,
    # :func:`_human_seat`). The two are INDEPENDENT: either can be known without
    # the other, which is why the human axis multiplies the dialogue kinds rather
    # than replacing them.
    sides = _cast_side_map(lines)
    human = _human_seat(lines)

    spans: list[tuple[str, str]] = []
    # PASS 2 — ``split("\n")`` + a plain ``"\n"`` span between consecutive lines
    # reconstructs the input exactly for every line ending, including a file with
    # no trailing newline (all 298 committed transcripts end on ``>``) and a
    # blank line (which contributes no span of its own, only the separators).
    for index, line in enumerate(lines):
        if index:
            spans.append(("\n", KIND_PLAIN))
        spans.extend(
            _line_spans(
                line, is_first_line=index == 0, sides=sides, human=human
            )
        )
    return _coalesce_spans(spans)


def _line_spans(
    line: str,
    *,
    is_first_line: bool,
    sides: Mapping[str, str],
    human: str | None,
) -> list[tuple[str, str]]:
    """The spans for one transcript line, excluding its separator.

    **The extension point for the later slices of spec 038.** The branches are an
    ordered priority chain — first match wins — so a new kind is added as one new
    branch (or, for content nested inside a tag, as one entry in
    :data:`_INLINE_CONTENT_KINDS`), and the final ``plain`` fallback keeps every
    line the chain does not recognise fully readable.

    ``is_first_line`` gates the ``Game N | …`` metadata line, which is a
    positional fact about the file rather than a shape a line can have anywhere.
    ``sides`` is the file's ``name → side`` cast map (:func:`_cast_side_map`),
    read-only and possibly empty, and ``human`` is the reviewer's own seat name
    or ``None`` (:func:`_human_seat`) — the **two** things here that are not
    facts about this one line, and the reason the same line can tokenize
    differently in two different files from Slice 4 onward. The function itself
    is still pure and keeps no memory between calls.

    **Order is load-bearing**, not incidental: the tag branch runs before the
    ``Round N.`` label, which runs before the cast-list field label, which runs
    before the ``Name:`` speaker rule, which runs before the plain fallback.

    The speaker rule's position is the one the whole chain exists to get right.
    ``Personality: brisk`` is shaped *exactly* like a player named "Personality"
    speaking, and so are the other four labels — put the speaker branch above
    the field-label branch and it swallows all **6,200** cast-list labels in the
    corpus, turning the opening cast list into a transcript of six people called
    Personality, Manner, Persona, Public legend and True self (hidden). The
    label reading is the right one, so the label branch keeps its place.
    """
    if is_first_line and _METADATA_LINE_RE.match(line):
        return [(line, KIND_MARKER)]

    # Leading indentation is always plain — it is layout, not content, and
    # keeping it out of the marker span is what lets the pre-spec-022 era's
    # indented ``  <round>`` / ``  Round N.`` lines tokenize exactly like the
    # flush-left spec-022 form.
    body = line.lstrip()
    indent = line[: len(line) - len(body)]
    prefix: list[tuple[str, str]] = [(indent, KIND_PLAIN)] if indent else []
    if not body:
        return prefix

    tag_spans = _tag_element_spans(body, sides)
    if tag_spans is not None:
        return prefix + tag_spans

    if _ROUND_LABEL_RE.match(body):
        return prefix + [(body, KIND_MARKER)]

    # A cast-list field: the label (colon included) is set apart, the prose after
    # it is not. This branch must stay **above** Slice 3's coming ``Name:``
    # speaker rule — ``Personality: brisk`` is shaped exactly like a player named
    # "Personality" speaking, and the label reading is the right one.
    label = _FIELD_LABEL_RE.match(body)
    if label is not None:
        return prefix + [
            (label.group(0), KIND_FIELD_LABEL),
            (body[label.end() :], KIND_PLAIN),
        ]

    # Somebody speaking: the ``Name:`` prefix and the words after it. Split on
    # the same boundary the field-label branch above uses — the prefix span ends
    # at its colon and everything after it, the separating space included, is
    # the next span — so the file holds one convention for "a label-ish span
    # ends at its colon" rather than two. The space is styling-invisible either
    # way (the UI sets ``color:`` / ``text-style:`` only, never a background),
    # so consistency is the only thing at stake and it decides it.
    speaker = _SPEAKER_RE.match(body)
    if speaker is not None and speaker.group("name") not in _NON_SPEAKER_PREFIXES:
        # Slice 4: the same two spans, kinded by the speaker's side when the cast
        # list knows it. ``sides.get`` returning ``None`` — a pre-spec-022 game
        # with no cast list to read, or a name absent from the one there is —
        # falls back to the NEUTRAL ``speaker``/``speech``, not to ``plain``:
        # "somebody is speaking" is a shape this line proves on its own, and only
        # the *side* is unknown. See :func:`tokenize_transcript`'s docstring for
        # why that is the right reading of "never a guess".
        side = sides.get(speaker.group("name"))
        speaker_kind = KIND_SPEAKER
        speech_kind = KIND_SPEECH
        if side is not None:
            # ``.get`` rather than ``[]``: the map can only hold the two sides
            # :data:`_ROLE_SIDES` produces, but "never raises on real input" is a
            # hard guarantee and a lookup is not the place to spend it.
            speaker_kind = _SIDE_SPEAKER_KINDS.get(side, KIND_SPEAKER)
            speech_kind = _SIDE_SPEECH_KINDS.get(side, KIND_SPEECH)
        # Slice 5: the reviewer's own seat keeps whichever kind the side axis
        # just chose and takes its human form — bold WITHIN the side colour, so
        # the seat is marked more strongly than its side-mates rather than
        # differently coloured (functional-spec §2: other same-side players'
        # lines are "the same colour but not bold"). Applied AFTER the side so
        # the neutral case composes too: a pre-spec-022 game names its seat in
        # the preamble and has no cast list at all, and that seat is still marked.
        # ``.get(kind, kind)`` — a kind with no human form is left exactly as it
        # is, and no kind name is ever manufactured.
        if human is not None and speaker.group("name") == human:
            speaker_kind = _HUMAN_KINDS.get(speaker_kind, speaker_kind)
            speech_kind = _HUMAN_KINDS.get(speech_kind, speech_kind)
        # ``_SPEAKER_RE``'s lookahead guarantees at least the separating space,
        # so the speech span is never empty.
        return prefix + [
            (speaker.group(0), speaker_kind),
            (body[speaker.end() :], speech_kind),
        ]

    return prefix + [(body, KIND_PLAIN)]


def _tag_element_spans(
    body: str, sides: Mapping[str, str]
) -> list[tuple[str, str]] | None:
    """Spans for a line that starts with a recognised structural tag, else ``None``.

    ``None`` means "not a tag line" — the caller then falls through the rest of
    its chain, so an unrecognised tag name (a tag some later format adds, or
    speech that happens to open with an angle bracket) is left to the plain
    fallback rather than styled as skeleton it is not. This docstring used to
    name ``<diary>`` as its example of exactly that; spec 039 recognises the tag
    now, which is the whitelist working as designed — a transcript written
    before the entry existed still tokenizes byte-for-byte as it did.

    The three shapes the writer emits (verified across all 298 committed
    transcripts — every ``<`` and ``>`` in the corpus belongs to one of them;
    spec 039's ``<diary player="…" day="…">…</diary>`` is the second shape, the
    same one ``<thought>`` has always taken):

    - ``<tag …>`` / ``</tag>`` alone on the line → one marker span;
    - ``<tag …>content</tag>`` inline → opening tag, content, closing tag, the
      content's kind taken from :data:`_INLINE_CONTENT_KINDS` (default plain);
    - defensively, an opening tag whose content is *not* closed on the same line
      (a shape the corpus does not contain, but a multi-line model-generated
      thought could produce) → marker for the tag, plain for the remainder.
    """
    match = _TAG_HEAD_RE.match(body)
    if match is None or match.group("name") not in _MARKER_TAGS:
        return None

    head = _tag_head_spans(match, sides)
    rest = body[match.end() :]
    if not rest:
        return head

    closing = f"</{match.group('name')}>"
    if not match.group("slash") and rest.endswith(closing):
        inner = rest[: -len(closing)]
        spans = [*head]
        if inner:
            # An empty body (``<night></night>``, which ``_wrap`` emits for a
            # section that captured nothing) contributes no span at all — the
            # no-empty-span guarantee.
            inner_kind = _INLINE_CONTENT_KINDS.get(
                match.group("name"), KIND_PLAIN
            )
            spans.append((inner, inner_kind))
        spans.append((closing, KIND_MARKER))
        return spans

    return [*head, (rest, KIND_PLAIN)]


def _tag_head_spans(
    match: re.Match[str], sides: Mapping[str, str]
) -> list[tuple[str, str]]:
    """One tag's ``<…>`` head, split into marker / attr / marker / attr / marker.

    ``match`` is a :data:`_TAG_HEAD_RE` match anchored at position 0 of the line
    body, so every offset it reports indexes the head text directly.

    An attribute-free tag (``<day>``, ``</player>``, ``<kill>``) yields the
    single :data:`KIND_MARKER` span it always did. A tag carrying one of
    :data:`_ATTR_NAMES` alternates: the punctuation up to and including
    ``name="`` is marker, the value is :data:`KIND_ATTR`, and so on, with the
    trailing ``">`` closing it out. So ::

        <player name="Avery" role="Mafia">

    becomes five spans — ``<player name="`` · ``Avery`` · ``" role="`` ·
    ``Mafia`` · ``">`` — which is functional-spec §2's "the name and the role are
    distinguishable from the surrounding marker text" made structural. Since
    Slice 4 the two values may take **different** kinds: here ``Avery`` is
    :data:`KIND_ATTR` and ``Mafia`` is :data:`KIND_ATTR_MAFIA`, so the cast list
    is coloured by side while the names stay achromatic (:func:`_attr_kind`).

    The pieces are emitted without checking for emptiness on purpose: an empty
    value (``name=""``) or a zero-length gap contributes an empty span that
    :func:`_coalesce_spans` drops at the single exit point, merging the markers
    on either side back into one. That keeps the no-empty-span guarantee in the
    one place that owns it, and keeps this function a pure re-slicing of
    ``match.group(0)`` — concatenation order never changes, so the round trip
    holds by construction.
    """
    attrs = match.group("attrs")
    head = match.group(0)
    if not attrs:
        return [(head, KIND_MARKER)]

    tag = match.group("name")
    offset = match.start("attrs")
    spans: list[tuple[str, str]] = []
    cursor = 0
    for value in _ATTR_VALUE_RE.finditer(attrs):
        start, end = value.span("value")
        spans.append((head[cursor : offset + start], KIND_MARKER))
        # Slice 4: two of the five values carry a side (see :func:`_attr_kind`);
        # the rest stay achromatic. Only the *kind* changes here — the slicing,
        # and therefore the round trip, is untouched.
        spans.append(
            (
                value.group("value"),
                _attr_kind(tag, value.group("key"), value.group("value"), sides),
            )
        )
        cursor = offset + end
    spans.append((head[cursor:], KIND_MARKER))
    return spans


def _coalesce_spans(spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop empty spans and merge adjacent same-kind runs into one span.

    Two jobs, both contract-level rather than cosmetic:

    - **empty spans are dropped**, so the no-empty-text guarantee holds by
      construction at the one place spans leave this module (an empty span is
      invisible to the round-trip check but is still junk for the UI to style);
    - **adjacent spans of the same kind are merged**, which guarantees no two
      neighbouring spans share a kind. That is what turns a Day's wall of speech
      into a single plain span instead of one per line — and it is what lets a
      ``<kill>Name — Side</kill>`` line, whose three parts are all
      :data:`KIND_MARKER`, come back as the single marker span the spec's table
      describes.

    Merging is always round-trip safe: concatenation order is unchanged and only
    the span boundaries move.
    """
    out: list[tuple[str, str]] = []
    for text, kind in spans:
        if not text:
            continue
        if out and out[-1][1] == kind:
            out[-1] = (out[-1][0] + text, kind)
        else:
            out.append((text, kind))
    return out


def build_table_model(records: list[RawRecord]) -> TableModel:
    """Flatten ``records`` into the index-parallel :class:`TableModel`.

    One row per record, columns = the fixed leading columns then one per
    :data:`METRIC_ORDER` entry. Every field is read through :func:`_dig` with a
    blank/fallback default, so the heterogeneous pre-provenance records (no
    ``code``, no ``settings``, no CI, game count under ``run.games``) flatten
    with blank ``⚠``/CI cells and the ``run.games`` fallback rather than raising
    (tech-spec 012 §2.1, §2.3).
    """
    columns = [*_FIXED_COLUMNS, *(label for _, label in METRIC_ORDER)]
    rows: list[list[str]] = []
    search_blobs: list[str] = []
    search_fields: list[dict[str, str]] = []

    for record in records:
        rows.append(_row_cells(record))
        search_blobs.append(_search_blob(record))
        search_fields.append(_search_fields(record))

    return TableModel(
        columns=columns,
        rows=rows,
        search_blobs=search_blobs,
        search_fields=search_fields,
        records=list(records),
    )


def _row_cells(record: RawRecord) -> list[str]:
    """Format one record's fixed + metric cells (plain strings), in column order."""
    dirty = bool(_dig(record, "code.dirty", default=False))
    # Identity models prefer the effective ``settings`` values (what actually
    # ran, post-override); pre-provenance records have no ``settings`` block, so
    # they fall back to the ``provider`` ids (``settings.* ?? provider.*``). The
    # ``_resolved_*`` helpers are the single source of that fallback (the search
    # blob and scoped-search fields call the same ones).
    large_model = _resolved_large_model(record)
    small_model = _resolved_small_model(record)
    games = _resolved_games(record)

    cells: list[str] = [
        "⚠" if dirty else "",
        _text(_dig(record, "run.date", "")),
        # Spec 036: the record kind, positioned to match ``_FIXED_COLUMNS``
        # (immediately after ``Date``) — defaults to ``game`` for the pre-036
        # records rather than blanking, since absence *means* a played game.
        _kind_cell(record),
        _text(_dig(record, "provider.name", "")),
        _text(large_model),
        _text(small_model),
        _text(games),
        _outcomes_cell(record),
        # Spec 029: positions MUST track ``_FIXED_COLUMNS`` exactly (the
        # ``len(row) == len(columns)`` invariant) — scripted-side + unresolved next
        # to ``Wins``, stand-in next to ``Lineup``.
        _scripted_side_cell(record),
        _resolution_cell(record),
        _vote_activity_cell(record),
        _stand_in_cell(record),
        # Spec 039: the diaries ARM, between ``Stand-in`` and ``Lineup`` — the
        # position ``_FIXED_COLUMNS`` declares (the record's own settings key
        # order). Blank for every record that does not state an arm.
        _diaries_cell(record),
        _lineup_cell(record),
        _note_cell(record),
    ]
    cells.extend(_metric_cell(record, dotted_key) for dotted_key, _ in METRIC_ORDER)
    return cells


def _note_cell(record: RawRecord) -> str:
    """The run's note as a single bounded line for the table cell.

    Collapses any newlines / runs of whitespace to single spaces and truncates to
    :data:`_NOTES_CELL_MAXLEN` with a trailing ellipsis, so a long multi-line note
    stays one tidy cell. The full note (verbatim, newlines preserved) lives in the
    drill-down (:func:`render_detail`); this is only the at-a-glance preview — and,
    because notes are part of the search blob (:func:`_search_blob`), it is what
    makes a note-match visible in the row rather than a phantom hit. An absent note
    yields the empty string.
    """
    collapsed = " ".join(_text(_dig(record, "notes", "")).split())
    if len(collapsed) > _NOTES_CELL_MAXLEN:
        return collapsed[: _NOTES_CELL_MAXLEN - 1].rstrip() + "…"
    return collapsed


def _metric_facets(record: RawRecord, dotted_key: str) -> dict[str, Any] | None:
    """Resolve a metric's facet map under ``metrics``, handling both key shapes.

    The real committed ledger stores a vote metric under a **flat dotted string
    key** — ``metrics['self_vote.initiation']`` is one literal key — because
    ``blunder_eval.render_record`` emits the metric name (``"self_vote.initiation"``)
    verbatim as a sub-key. A genuinely **nested** shape
    (``metrics['self_vote']['initiation']``) is also accepted defensively, so a
    future writer that nests the vote families still resolves. The flat literal
    key is tried first (the on-disk shape), then the nested path. Speech metrics
    (``repetition``, ``third_person_self_talk``) have no dot, so the two lookups
    coincide. Returns the facet mapping, or ``None`` when the metric is absent.
    """
    metrics = record.get("metrics") if isinstance(record, dict) else None
    if not isinstance(metrics, dict):
        return None
    # On-disk shape: the dotted name is one literal key under ``metrics``.
    flat = metrics.get(dotted_key)
    if isinstance(flat, dict):
        return flat
    # Defensive fallback: a genuinely nested ``metrics.<a>.<b>`` map.
    nested = _dig(metrics, dotted_key)
    return nested if isinstance(nested, dict) else None


def _metric_cell(record: RawRecord, dotted_key: str) -> str:
    """Format one metric cell — empty when the metric is absent for this run.

    Resolves the metric's facet map via :func:`_metric_facets` (which absorbs the
    flat-dotted-key vs nested-map heterogeneity) and formats it per tech-spec 012
    §2.3:

    - **Absent metric** (the family/key is not in this run's ``metrics``) → the
      **empty string** — a metric the game never had the chance to exercise,
      which must stay visibly distinct from a genuine zero.
    - **Present with CI** → ``rate [ci_low–ci_high] count/denominator`` (e.g.
      ``0.45 [0.36–0.55] 49/108``).
    - **Present without CI** (a pre-CI record that legitimately lacks the band)
      → the bracketed band is omitted → ``rate count/denominator`` (e.g.
      ``0.45 49/108``). A clean ``0.00 0/108`` still renders non-empty, so it
      stays distinct from the absent-metric blank.
    - **Value-type metric** (the persona similarity facets ``persona_lex_mean`` /
      ``persona_sem_mean`` — a ``mean``/``denominator`` facet; ``persona_lex_peak``
      / ``persona_sem_peak`` — a ``peak``/``denominator`` facet — each with no
      ``rate``) → ``~<value> (n=<denominator>)`` (e.g. ``~0.42 (n=10)``); the ``~``
      signals a similarity value (mean or peak) and ``n`` is the pair count. No CI
      band (neither is a binomial rate).
    """
    facets = _metric_facets(record, dotted_key)
    if facets is None:
        return ""

    # Value-type metric (the persona ``*_mean`` / ``*_peak`` facets): a ``mean`` or
    # ``peak`` value with no ``rate`` (a similarity, not a binomial proportion).
    value = facets.get("mean")
    if value is None:
        value = facets.get("peak")
    if value is not None and facets.get("rate") is None:
        denom = facets.get("denominator")
        if denom is None:
            return ""
        return f"~{float(value):.2f} (n={denom})"

    rate = facets.get("rate")
    count = facets.get("count")
    denom = facets.get("denominator")
    if rate is None or count is None or denom is None:
        return ""

    head = f"{float(rate):.2f}"
    ci_low = facets.get("ci_low")
    ci_high = facets.get("ci_high")
    if ci_low is not None and ci_high is not None:
        band = f" [{float(ci_low):.2f}{_CI_DASH}{float(ci_high):.2f}]"
    else:
        band = ""
    return f"{head}{band} {count}/{denom}"


def _search_blob(record: RawRecord) -> str:
    """Build one lowercased searchable string for a record (tech-spec 012 §2.4).

    Concatenates the run's date, provider name, both resolved model ids, the
    code commit and branch, a ``dirty``/``clean`` keyword, the recorded
    ``run.kind`` (spec 036 — **only when present**, so a bench record is findable
    by typing ``persona-bench`` without seeding every older record with a
    ``game`` token), the derived ``winner`` keyword (the side that won the run's
    majority — :func:`_winner_keyword` — empty on a pre-013 record), and the
    **full notes** text — the facets the
    viewer's substring filter searches across. Model ids
    prefer the effective ``settings`` values with the ``provider`` fallback (same
    rule as the row cells), so a pre-provenance record still contributes its
    model ids to the blob. Everything is lowercased so the UI's per-keystroke
    ``query in blob`` test is case-insensitive.
    """
    dirty = bool(_dig(record, "code.dirty", default=False))
    parts = [
        _text(_dig(record, "run.date", "")),
        _text(_dig(record, "provider.name", "")),
        _text(_resolved_large_model(record)),
        _text(_resolved_small_model(record)),
        _text(_dig(record, "code.commit", "")),
        _text(_dig(record, "code.branch", "")),
        "dirty" if dirty else "clean",
        # Spec 036: the record kind, contributed to the free-text blob ONLY when
        # the record actually carries ``run.kind`` — the ``_winner_keyword``
        # posture ("empty on an older record, so the keyword neither matches nor
        # pollutes the blob"). Deliberately NOT the ``game`` default the ``Kind``
        # cell shows: seeding every one of the pre-036 records with the token
        # ``game`` would make a free-text ``game`` query match the entire ledger.
        # Filtering to the played games is what the *scoped* ``kind`` field is
        # for; the blob's job here is to make a bench record findable by typing
        # ``persona-bench`` under "All".
        _text(_dig(record, "run.kind", "")),
        _winner_keyword(record),
        _lineup_keyword(record),
        _text(_dig(record, "notes", "")),
    ]
    return " ".join(part for part in parts if part).lower()


def _search_fields(record: RawRecord) -> dict[str, str]:
    """Build the per-row scoped-search field map (tech-spec 012 §2.4).

    One ``dict[str, str]`` keyed by the canonical :data:`SEARCH_FIELDS` names,
    each value the record's **lowercased** searchable text for that one field —
    so a ``field:value`` term in :func:`row_matches` checks ``value`` as a
    substring of only the named field. Every read reuses the same defensive
    :func:`_dig` extraction (and the shared ``_resolved_*`` model/games fallback)
    as the row cells / :func:`_search_blob`, so the scoped fields can never go out
    of sync with what the row shows.

    ``model`` deliberately joins **both** resolved model ids (large + small) so a
    single ``model`` search matches either tier. ``state`` carries the
    ``dirty``/``clean`` keyword derived from ``code.dirty``. ``kind`` (spec 036)
    carries exactly what the ``Kind`` cell shows (:func:`_kind_cell`) — the
    recorded ``run.kind``, or the ``game`` default for a record that has none —
    so scoping to ``kind`` and typing ``game`` selects the played games while
    ``persona-bench`` selects the bench measurements. This is the one field whose
    text is deliberately *richer* than the blob's contribution, which stays
    present-only (see :func:`_search_blob`). ``winner`` carries the
    derived majority-side keyword (:func:`_winner_keyword` — ``law_abiding`` /
    ``mafia`` / ``draw`` / ``mixed``, empty on a pre-013 record); there is
    deliberately **no** vote-activity field (a count, not a searchable keyword).
    """
    dirty = bool(_dig(record, "code.dirty", default=False))
    models = " ".join(
        part
        for part in (
            _text(_resolved_large_model(record)),
            _text(_resolved_small_model(record)),
        )
        if part
    )
    fields = {
        "provider": _text(_dig(record, "provider.name", "")),
        "date": _text(_dig(record, "run.date", "")),
        # Spec 036: the same resolved text :func:`_kind_cell` puts in the row, so
        # a scoped ``kind`` search can never disagree with the visible cell —
        # including the ``game`` default a pre-036 record reads.
        "kind": _kind_cell(record),
        "model": models,
        "commit": _text(_dig(record, "code.commit", "")),
        "branch": _text(_dig(record, "code.branch", "")),
        "games": _text(_resolved_games(record)),
        "note": _text(_dig(record, "notes", "")),
        "state": "dirty" if dirty else "clean",
        "winner": _winner_keyword(record),
    }
    return {key: value.lower() for key, value in fields.items()}


def row_matches_field(
    field: str, value: str, blob: str, fields: dict[str, str]
) -> bool:
    """Does this row satisfy the selector ``field`` + typed ``value``? (§2.4)

    The pure matcher behind the viewer's filter. The **field selector** chooses
    the haystack: :data:`SEARCH_SCOPE_ALL` searches the whole free-text ``blob``
    (every fact); any :data:`SEARCH_FIELDS` name scopes to that one field's text
    (``fields[field]``). The typed ``value`` is lowercased and split on whitespace
    into terms that are **ANDed** — every term must be a substring of the chosen
    haystack — so an empty / all-whitespace value keeps every row.

    There is **no ``field:value`` parsing**: scoping is the selector's job, not a
    syntax in the text box (that was the awkward "type the field name and it
    matches nothing until the colon" UX this replaced). A colon in ``value`` is
    therefore matched literally, so a model id like ``qwen3-coder:30b`` searches
    as written.
    """
    haystack = blob if field == SEARCH_SCOPE_ALL else fields.get(field, "")
    return all(term in haystack for term in value.lower().split())


def _text(value: Any) -> str:
    """Render a scalar as display text — ``""`` for ``None``, else ``str``.

    A ``None`` (a YAML ``null`` field, or a :func:`_dig` default) renders as the
    empty string rather than the literal ``"None"``; everything else is stringified.
    """
    return "" if value is None else str(value)


# Placeholder for an absent scalar field in the detail render — a typographic
# em-dash reading "this field was not recorded for this run", consistently used
# everywhere a single value is missing on a pre-provenance record.
_ABSENT = "—"


def render_detail(record: RawRecord) -> str:
    """Render one ledger record as a readable, sectioned full-record view.

    A plain ``str`` (newline-joined) — **not** a YAML re-dump — laying every
    provenance and quality field out under section headers in the canonical
    top-level order ``run`` → ``code`` → ``provider`` → ``settings`` →
    ``quality`` → ``outcomes`` → ``vote_activity`` → ``generation`` →
    ``metrics`` → ``notes`` (tech-spec 012 §2.5, extended by 013 §2.3 and by
    036 §2 C — the two game-dynamics blocks and the bench-only ``generation``
    block all sit after ``quality`` and before ``metrics``, matching the record
    key order ``render_record`` writes). The
    thin Textual ``DetailScreen`` (a later task) wraps this string in a scroller;
    **no Rich/Textual concern lives here**, mirroring the table model's
    plain-string contract.

    Defensive throughout: every field is read via :func:`_dig`, so a
    *pre-provenance* record (no ``code`` / ``settings`` blocks, no CI bands,
    game count under ``run.games``) renders without raising — an absent scalar
    shows as :data:`_ABSENT` (``—``) and a whole absent sub-block collapses to a
    single ``—`` line. A ``KeyError`` never escapes.

    What each section shows:

    - **run** — date, the spec-036 ``kind`` when present (absent ⇒ a played
      game, and no line is shown), ``duration_seconds``, ``metrics_version``, and
      the ``run.games`` fallback count when present.
    - **code** — ``commit``, ``branch``, and the working-copy state spelled out
      as ``dirty`` / ``clean`` (the table only flags it with ``⚠``).
    - **provider** — ``name`` and the resolved model ids, then the
      shape-specific extras: ollama ``models`` digests + ``server_version``, or
      the bedrock ``note``.
    - **settings** — the effective resolved values incl. ``games``, plus
      ``metrics_version`` mirrored here for a like-for-like repeat, plus two
      conditional one-line fields between ``max_days`` and the lineup —
      ``scripted_player`` (spec 026) and ``private_diaries_enabled`` (spec 039,
      the arm of the private-diaries A/B; **absent ⇒ no line, never ``False``**)
      — plus — spec 036, only when present — a ``persona`` sub-block naming the
      conditions a persona-generation measurement ran under
      (``diversity_enabled``, ``collision_threshold``, ``regen_attempts``,
      ``temperature``). A record without a field gains no line, so every already-
      committed record reads as before.
    - **quality** — the run-quality counts, plus — spec 039, only when the run
      attempted a diary entry — the three flat ``diary_*`` run-health keys
      (``diary_fallback_rate``, ``diary_fallback_entries``,
      ``diary_entries_attempted``) between ``games_failed_early`` and
      ``duration_seconds``. Omitted together when no entry was attempted, so
      absence reads as "no opportunity", never as a clean ``0.0``.
    - **outcomes** — the win-rate by side (013 §2.1): ``games``, then
      ``law_abiding``/``mafia`` each with ``wins`` + **full-precision** ``rate`` +
      a ``[ci_low–ci_high]`` band (rate/band omitted on the ``games == 0`` path),
      then — spec 027, only when present — a ``scripted_side`` sub-block (the
      seat's ``side`` + ``wins`` + ``rate``/band), then the bare
      ``draw``/``no_winner`` counts and the immutable ``note`` caveat. A whole
      absent block (pre-013 record) collapses to one ``—`` line.
    - **vote_activity** — AI vote-initiation counts (013 §2.2): a ``by_side``
      sub-block (**both** sides always, the explicit-zero) and a ``by_day``
      sub-block (``day_N: n`` sorted by integer suffix, or a ``(none)`` line when
      empty so "present but no per-day activity" stays distinct from an absent
      block, which collapses to one ``—`` line).
    - **generation** — the persona-generation process counts (036 §2 A):
      ``collisions`` (casts that ended with an over-similar pair) and
      ``regenerations``. Present only on a persona-bench record; a played game
      (and any pre-036 record) collapses to one ``—`` line, the mirror image of
      ``outcomes``/``vote_activity`` on a bench record.
    - **metrics** — one line per :data:`METRIC_ORDER` entry (so order and
      vocabulary match the table's columns). Each **present** metric shows its
      **full-precision** ``rate`` + ``[ci_low–ci_high]`` band (band omitted when
      CI is absent, mirroring the table cell) + ``count/denominator``; an
      **absent** metric shows ``—`` so a never-exercised metric stays visibly
      distinct from a genuine ``0.0``.
    - **notes** — the complete free-text note **verbatim**, newlines preserved.
    """
    sections: list[str] = [
        _render_run_section(record),
        _render_code_section(record),
        _render_provider_section(record),
        _render_settings_section(record),
        _render_quality_section(record),
        _render_outcomes_section(record),
        _render_vote_activity_section(record),
        _render_generation_section(record),
        _render_metrics_section(record),
        _render_notes_section(record),
    ]
    return "\n\n".join(sections)


def _section(title: str, lines: list[str]) -> str:
    """Join a section header with its ``label: value`` lines (one block)."""
    return "\n".join([title, *lines])


def _field(label: str, value: Any, *, indent: int = 1) -> str:
    """One ``label: value`` line; an absent (``None``/blank) value shows ``—``.

    ``indent`` is the nesting depth in two-space units: ``1`` (the default, and
    what every top-level section field uses) renders ``"  label: value"``, and
    ``2`` renders a field *inside* a sub-block at four spaces — the same depth the
    hand-rolled ``outcomes.scripted_side`` / ``vote_activity.by_side`` sub-block
    lines already emit. The default is deliberately behaviour-preserving, so
    adding the parameter changes not one byte of any existing section.
    """
    text = _text(value)
    return f"{'  ' * indent}{label}: {text if text else _ABSENT}"


def _render_run_section(record: RawRecord) -> str:
    lines = [_field("date", _dig(record, "run.date"))]
    # Spec 036: ``run.kind`` names the KIND of measurement (e.g.
    # ``persona-bench``) and, per the unit-follows-kind rule, tells the reader
    # that ``quality.attempted``/``completed`` count rosters rather than games.
    # Rendered right after ``date``, matching the record's own key order, and
    # CONDITIONAL like the ``games`` line below — a record without the key is a
    # played game and gains no line at all, so every pre-036 record's detail view
    # is unchanged. Without this the drill-down showed no ``kind`` anywhere, even
    # though the table already had its ``Kind`` column.
    kind = _dig(record, "run.kind")
    if kind is not None:
        lines.append(_field("kind", kind))
    lines += [
        _field("duration_seconds", _dig(record, "run.duration_seconds")),
        _field("metrics_version", _dig(record, "run.metrics_version")),
    ]
    # Pre-provenance records carry the game count under ``run.games`` (later
    # records moved it to ``settings.games``). Surface it here only when present
    # so the run block stays faithful to the on-disk shape.
    run_games = _dig(record, "run.games")
    if run_games is not None:
        lines.append(_field("games", run_games))
    return _section("run", lines)


def _render_code_section(record: RawRecord) -> str:
    code = _dig(record, "code")
    if not isinstance(code, dict):
        # Whole block absent on a pre-provenance record — collapse to one line.
        return _section("code", [f"  {_ABSENT}"])
    # ``dirty`` is the load-bearing flag: spell out the working-copy state in
    # words (the table only shows a ``⚠`` marker). A missing ``dirty`` reads as
    # unknown rather than silently "clean".
    dirty = _dig(record, "code.dirty", default=_MISSING)
    if dirty is _MISSING:
        state = _ABSENT
    else:
        state = "dirty" if bool(dirty) else "clean"
    return _section(
        "code",
        [
            _field("commit", _dig(record, "code.commit")),
            _field("branch", _dig(record, "code.branch")),
            f"  working copy: {state}",
        ],
    )


def _render_provider_section(record: RawRecord) -> str:
    lines = [
        _field("name", _dig(record, "provider.name")),
        _field("large_model", _dig(record, "provider.large_model")),
        _field("small_model", _dig(record, "provider.small_model")),
    ]
    # Shape-specific extras: ollama carries per-model digests + a server version;
    # bedrock carries a fixed caveat ``note`` instead. Show whichever is present.
    models = _dig(record, "provider.models")
    if isinstance(models, dict) and models:
        lines.append("  models:")
        for name, info in models.items():
            digest = _dig(info, "digest")
            lines.append(f"    {name}: {_text(digest) or _ABSENT}")
    server_version = _dig(record, "provider.server_version")
    if server_version is not None:
        lines.append(_field("server_version", server_version))
    note = _dig(record, "provider.note")
    if note is not None:
        lines.append(_field("note", note))
    return _section("provider", lines)


# The persona knobs a bench record carries under ``settings.persona`` (spec 036
# §2 Component B), in the order ``blunder_eval.render_record`` writes them — so
# the drill-down reads the same way round as the file on disk. Kept as one tuple
# rather than four call sites: the writer's key list and the reader's field list
# are the same contract, and a knob added by a future spec surfaces as one
# appended name here.
_PERSONA_SETTING_FIELDS: tuple[str, ...] = (
    # The ARM the run was actually invoked with (the bench's ``--diversity``
    # flag), not the ambient config default. This is the load-bearing one: it is
    # what makes a flag-on / flag-off pair readable AS A PAIR (functional-spec
    # §2), and it was the field a reader could not see before this branch existed.
    "diversity_enabled",
    "collision_threshold",
    "regen_attempts",
    "temperature",
)


def _render_settings_section(record: RawRecord) -> str:
    """The ``settings`` block — the effective run conditions, or one ``—`` line.

    A whole **absent** ``settings`` block (any pre-provenance record) collapses to
    a single ``—`` line. The flat fields are rendered unconditionally (an absent
    one shows ``—``); the spec-036 ``persona`` sub-map is rendered **only when
    present**, for the same reason ``outcomes.scripted_side`` is: four
    unconditional em-dash lines would be added to the drill-down of every one of
    the already-committed game records, which is a visible regression, not a
    faithful render. Present ⇒ four indented lines; absent ⇒ **no line at all**,
    so a game record's detail view is byte-identical to before.

    **Two more conditional one-line fields sit between ``max_days`` and the
    lineup**, in the record's own key order (``render_record`` emits
    ``scripted_player`` → ``private_diaries_enabled`` → the nested ``lineup``):

    - ``scripted_player`` (spec 026) — written to every record since spec 026 and,
      until now, **never rendered here**: it existed only as the table's
      ``Stand-in`` column. Exactly the "written to the record and invisible in the
      drill-down" gap the spec-036 follow-up closed for ``run.kind``,
      ``generation`` and ``settings.persona``, left open for this one field.
    - ``private_diaries_enabled`` (spec 039) — the arm of the private-diaries A/B
      the run measured. **Absence renders as no line at all, never ``False``**: a
      pre-039 record was played by a build with no diary feature, so a ``False``
      would assert a measurement nobody made (see :func:`_diaries_cell`).

    Both are conditional for the same reason as ``persona``: unconditional lines
    would print ``—`` on records that never carried the field — ``scripted_player``
    on the pre-026 ones, ``private_diaries_enabled`` on all 30 committed records.
    A present ``False`` still renders (``_text(False)`` is ``"False"``, not blank),
    so the diaries-**off** arm reads as the measurement it is — the same trap
    ``persona.diversity_enabled`` documents.
    """
    settings = _dig(record, "settings")
    if not isinstance(settings, dict):
        return _section("settings", [f"  {_ABSENT}"])
    lines = [
        _field("large_model", _dig(record, "settings.large_model")),
        _field("small_model", _dig(record, "settings.small_model")),
        _field("base_url", _dig(record, "settings.base_url")),
        _field("games", _dig(record, "settings.games")),
        _field("seed", _dig(record, "settings.seed")),
        # Spec 023: the game-length control was renamed ``max_rounds`` →
        # ``max_days`` (the runaway Day cap). New records carry
        # ``settings.max_days``; already-committed pre-023 records carry the
        # old ``settings.max_rounds``. Render the new field, falling back to
        # the legacy one, so heterogeneous records all stay readable.
        _field(
            "max_days",
            _dig(
                record,
                "settings.max_days",
                default=_dig(record, "settings.max_rounds"),
            ),
        ),
    ]
    # Spec 026, rendered here for the first time by spec 039's Slice 5: the
    # human-seat stand-in mode. CONDITIONAL, because a pre-026 record does not
    # carry the field and would otherwise gain an ``—`` line — but note the
    # asymmetry with the ``Stand-in`` table cell, which *defaults* an absent
    # field to ``passive``. The cell can do that because a column of blanks
    # would read as an unfilled field; a drill-down line cannot, because adding
    # one to records that never carried the field is the visible regression this
    # function's own docstring rules out. Absent ⇒ no line; the ``evals/README.md``
    # contract (absent ⇒ read as ``passive``) is where that reading lives.
    scripted_player = _dig(record, "settings.scripted_player")
    if scripted_player is not None:
        lines.append(_field("scripted_player", scripted_player))
    # Spec 039 §2.10: the diaries ARM the run measured, straight after
    # ``scripted_player`` and before the lineup — the record's own key order.
    # CONDITIONAL, and for this field absence is a THIRD case rather than a falsy
    # one: not ``_STAND_IN_DEFAULT``'s "the prior default" and not
    # ``_KIND_DEFAULT``'s "a played game", but "the build that played this run had
    # no diary feature at all". So it gains no line and no ``False`` — which would
    # claim the control arm of an experiment that did not exist yet.
    private_diaries_enabled = _dig(record, "settings.private_diaries_enabled")
    if private_diaries_enabled is not None:
        lines.append(_field("private_diaries_enabled", private_diaries_enabled))
    lines += [
        # Spec-014 lineup, defensively dug — a pre-014 record (no
        # ``settings.lineup``) shows the ``—`` em-dash, no migration.
        _field("citizens", _dig(record, "settings.lineup.num_citizens")),
        _field("mafia", _dig(record, "settings.lineup.num_mafia")),
    ]
    # Spec 036: the persona knobs the measurement ran under, as a nested sub-block
    # after the lineup — mirroring the record's own key order (``render_record``
    # emits ``settings.persona`` directly after ``settings.lineup``). CONDITIONAL,
    # like ``outcomes.scripted_side``: the sub-block writes itself only when the
    # record carries it, so no game record gains a line and no migration is
    # needed. Without this branch the four knobs were written to the record and
    # invisible in the drill-down — the same non-iterating-renderer gap that hid
    # ``run.kind`` and the whole ``generation`` block, and one that bit a
    # functional-spec criterion directly (``diversity_enabled`` is what a
    # side-by-side flag-on/flag-off pair is read *by*).
    persona = _dig(record, "settings.persona")
    if isinstance(persona, dict):
        lines.append("  persona:")
        lines += [
            _field(name, _dig(record, f"settings.persona.{name}"), indent=2)
            for name in _PERSONA_SETTING_FIELDS
        ]
    return _section("settings", lines)


# The three flat ``quality.diary_*`` keys (spec 039 §2.13), in the order
# ``render_record`` inserts them — rate, then the count, then the denominator it
# was taken over. One tuple rather than three call sites, following
# :data:`_PERSONA_SETTING_FIELDS`: the writer's key list and the reader's field
# list are the same contract.
#
# The **rate is read, never recomputed.** ``render_record`` derives it once from
# the count/denominator pair precisely so there is one definition of it; deriving
# it a second time here would create a second, and a record whose stored rate
# disagreed with its own operands would then render as though it agreed.
_DIARY_QUALITY_FIELDS: tuple[str, ...] = (
    "diary_fallback_rate",
    "diary_fallback_entries",
    "diary_entries_attempted",
)

# The key the whole ``diary_*`` trio is gated on — the DENOMINATOR, mirroring the
# writer's own gate (``render_record`` emits all three only when entries were
# attempted). Gating on the denominator rather than on the rate is the
# absent-not-zero rule stated from the reading side: a ``diary_fallback_rate`` of
# ``0.0`` is a real, clean measurement, so presence can never be inferred from
# truthiness, and a rate is by contract never written without its denominator
# beside it.
_DIARY_QUALITY_GATE = "quality.diary_entries_attempted"


def _render_quality_section(record: RawRecord) -> str:
    """The ``quality`` block — run-health counts, or one ``—`` line.

    A whole **absent** ``quality`` block collapses to a single ``—`` line. The
    four original counts render unconditionally; spec 039's three flat
    ``diary_*`` keys render **only when the run attempted a diary entry**,
    positioned between ``games_failed_early`` — the other "did this run measure
    anything?" count — and ``duration_seconds``, matching the record's own key
    order.

    **They are run health, not an AI-quality metric**, which is why they live here
    and not in ``metrics``: ``quality`` is a *census of one run* rather than a
    sample of a population — ``games_failed_early`` is an exact count — so nothing
    in this block carries a Wilson band, and ``METRIC_ORDER`` and the metric tail
    are untouched. The observed fallbacks also cluster by fan-out (a whole Day's
    entries fail together), violating the independent-Bernoulli assumption a
    Wilson band rests on, in the direction that would make the band a lie.

    **What they are for.** The diary node falls back to a deterministic
    placeholder when structured output fails, and a run that measured mostly
    placeholder text is *not a measurement of diary content* — a 1-game smoke
    produced 9 of 11 placeholder entries with nothing in the ledger, the
    transcript or the log saying so. Reading ``diary_fallback_rate`` beside its
    ``diary_entries_attempted`` denominator is what makes a diaries-on arm
    self-validating: a high rate is visibly not a measurement of diaries, where
    before it was indistinguishable from a clean one.

    **Absent, never a misleading zero.** All three are omitted together when no
    entry was attempted — a diaries-off arm, or any pre-039 record — so absence
    means "no opportunity", exactly as it does for a denominator-0 metric. The
    trio is therefore gated on the **denominator** (:data:`_DIARY_QUALITY_GATE`),
    never on the rate: a genuine ``0.0`` is the *clean* reading and must stay
    distinct from absence.
    """
    quality = _dig(record, "quality")
    if not isinstance(quality, dict):
        return _section("quality", [f"  {_ABSENT}"])
    lines = [
        _field("games_attempted", _dig(record, "quality.games_attempted")),
        _field("games_completed", _dig(record, "quality.games_completed")),
        _field("games_failed_early", _dig(record, "quality.games_failed_early")),
    ]
    if _dig(record, _DIARY_QUALITY_GATE, _MISSING) is not _MISSING:
        lines += [
            _field(name, _dig(record, f"quality.{name}"))
            for name in _DIARY_QUALITY_FIELDS
        ]
    lines.append(_field("duration_seconds", _dig(record, "quality.duration_seconds")))
    return _section("quality", lines)


def _render_outcomes_section(record: RawRecord) -> str:
    """The ``outcomes`` block — win-rate by side (013 §2.1), or one ``—`` line.

    A whole **absent** block (any pre-013 record) collapses to a single ``—``
    line, mirroring :func:`_render_code_section`'s absent pattern. When present:
    ``games``, then ``law_abiding``/``mafia`` each as ``wins`` + **full-precision**
    ``rate`` + a ``[ci_low–ci_high]`` band (rate + band omitted on the
    ``games == 0`` path, where only ``wins`` is recorded), then — spec 027, only
    when present — a ``scripted_side`` sub-block (``side`` + ``wins`` +
    full-precision ``rate``/band), then the bare ``runaway`` (spec 023, in-game
    Day cap) / ``draw`` / ``no_winner`` counts and the immutable ``note`` caveat.
    A pre-027 record (no ``outcomes.scripted_side``) simply omits the sub-block —
    no extra line. Every read is defensive (:func:`_dig`), so a malformed/partial
    block never raises.
    """
    outcomes = _dig(record, "outcomes")
    if not isinstance(outcomes, dict):
        return _section("outcomes", [f"  {_ABSENT}"])

    lines = [_field("games", _dig(record, "outcomes.games"))]
    for side in ("law_abiding", "mafia"):
        lines.append(f"  {side}:")
        wins = _dig(record, f"outcomes.{side}.wins")
        lines.append(f"    wins: {_text(wins) if _text(wins) else _ABSENT}")
        lines.append(f"    rate: {_format_outcome_rate(record, side)}")
    # Spec 027: the scripted stand-in's-side win rate — rendered after the two
    # sides and before ``runaway``, ONLY when present. A pre-027 record (or any
    # record whose run resolved no seat side) omits the key, so the defensive
    # ``_dig`` resolves to absent and this whole sub-block is skipped — no new
    # ``—`` line, the section is byte-identical to before for older records.
    scripted = _dig(record, "outcomes.scripted_side")
    if isinstance(scripted, dict):
        lines.append("  scripted_side:")
        side_label = _dig(record, "outcomes.scripted_side.side")
        lines.append(
            f"    side: {_text(side_label) if _text(side_label) else _ABSENT}"
        )
        wins = _dig(record, "outcomes.scripted_side.wins")
        lines.append(f"    wins: {_text(wins) if _text(wins) else _ABSENT}")
        lines.append(f"    rate: {_format_scripted_side_rate(record)}")
    # Spec 023: ``runaway`` (the in-game Day-cap hit) is a new bare-count bucket,
    # rendered before ``draw``. A pre-023 record (no ``outcomes.runaway``) shows
    # the ``—`` em-dash defensively — no migration.
    lines.append(_field("runaway", _dig(record, "outcomes.runaway")))
    lines.append(_field("draw", _dig(record, "outcomes.draw")))
    lines.append(_field("no_winner", _dig(record, "outcomes.no_winner")))
    lines.append(_field("note", _dig(record, "outcomes.note")))
    return _section("outcomes", lines)


def _format_outcome_rate(record: RawRecord, side: str) -> str:
    """A side's full-precision ``rate [ci_low–ci_high]`` band, or ``—``.

    Mirrors :func:`_format_detail_metric`'s full-precision posture (``repr`` of the
    float, not the table's two-decimal): an **absent** ``rate`` (the
    ``games == 0`` path omits it) shows :data:`_ABSENT`; a present ``rate`` shows
    the bare value, with the ``[ci_low–ci_high]`` band appended only when both CI
    bounds are present (omitted otherwise, like the metric detail).
    """
    rate = _dig(record, f"outcomes.{side}.rate")
    if rate is None:
        return _ABSENT
    ci_low = _dig(record, f"outcomes.{side}.ci_low")
    ci_high = _dig(record, f"outcomes.{side}.ci_high")
    if ci_low is not None and ci_high is not None:
        band = f" [{repr(float(ci_low))}{_CI_DASH}{repr(float(ci_high))}]"
    else:
        band = ""
    return f"{repr(float(rate))}{band}"


def _format_scripted_side_rate(record: RawRecord) -> str:
    """The scripted-side full-precision ``rate [ci_low–ci_high]`` band, or ``—`` (spec 027).

    Mirrors :func:`_format_outcome_rate` exactly (full-precision ``repr`` of the
    float, the ``[ci_low–ci_high]`` band appended only when both bounds are
    present), but reads off the ``outcomes.scripted_side`` sub-block. An absent
    ``rate`` (the ``games == 0`` path emits only ``side``/``wins``) shows
    :data:`_ABSENT`. Caller only invokes this when the sub-block is present.
    """
    rate = _dig(record, "outcomes.scripted_side.rate")
    if rate is None:
        return _ABSENT
    ci_low = _dig(record, "outcomes.scripted_side.ci_low")
    ci_high = _dig(record, "outcomes.scripted_side.ci_high")
    if ci_low is not None and ci_high is not None:
        band = f" [{repr(float(ci_low))}{_CI_DASH}{repr(float(ci_high))}]"
    else:
        band = ""
    return f"{repr(float(rate))}{band}"


def _render_vote_activity_section(record: RawRecord) -> str:
    """The ``vote_activity`` block — initiation counts (013 §2.2), or one ``—`` line.

    A whole **absent** block (pre-013 record) collapses to a single ``—`` line.
    When present: a ``by_side`` sub-block listing **both** sides always (the
    explicit-zero guarantee — a silent run reads ``law_abiding: 0`` /
    ``mafia: 0``), then a ``by_day`` sub-block listing ``day_N: n`` **sorted by
    integer suffix**. An empty ``by_day`` (present block, no per-day activity)
    shows a ``(none)`` line so it stays distinct from an absent block's ``—``.
    """
    activity = _dig(record, "vote_activity")
    if not isinstance(activity, dict):
        return _section("vote_activity", [f"  {_ABSENT}"])

    lines = ["  by_side:"]
    for side in ("law_abiding", "mafia"):
        lines.append(
            f"    {side}: {_vote_count(_dig(record, f'vote_activity.by_side.{side}'))}"
        )

    lines.append("  by_day:")
    by_day = _dig(record, "vote_activity.by_day")
    if isinstance(by_day, dict) and by_day:
        for day_key in sorted(by_day, key=_day_sort_key):
            lines.append(f"    {day_key}: {_vote_count(by_day[day_key])}")
    else:
        # Present block, no per-day activity — distinct from an absent block.
        lines.append("    (none)")
    return _section("vote_activity", lines)


def _day_sort_key(day_key: str) -> tuple[int, str]:
    """Sort ``day_N`` keys by integer suffix (so ``day_10`` follows ``day_2``).

    Falls back to lexical order (suffix second in the tuple) for any key that does
    not parse as ``day_<int>``, so a malformed key never raises.
    """
    _, _, suffix = day_key.partition("_")
    try:
        return (int(suffix), day_key)
    except ValueError:
        return (1 << 30, day_key)


def _render_generation_section(record: RawRecord) -> str:
    """The ``generation`` block — persona-generation process counts (spec 036), or ``—``.

    A whole **absent** block — every played game, and every record written before
    spec 036 — collapses to a single ``—`` line, exactly as
    :func:`_render_outcomes_section` does for a pre-013 record (and as it now
    does for a bench record, which plays no game). When present it shows
    ``collisions`` (how many casts ended with an over-similar persona pair) and
    ``regenerations`` (how many regeneration attempts fired).

    Read this beside the persona similarity facets in ``metrics``, never instead
    of them: the collision **count** is the figure that carried the spec-034
    comparison — 2-in-10 rosters shipping a near-duplicate versus 0-in-10 — which
    a similarity mean alone would have lost. A present ``collisions: 0`` is a
    measured finding, so the block renders its zeroes rather than omitting them.
    """
    generation = _dig(record, "generation")
    if not isinstance(generation, dict):
        return _section("generation", [f"  {_ABSENT}"])
    return _section(
        "generation",
        [
            _field("collisions", _dig(record, "generation.collisions")),
            _field("regenerations", _dig(record, "generation.regenerations")),
        ],
    )


def _render_metrics_section(record: RawRecord) -> str:
    """One ``label: value`` line per :data:`METRIC_ORDER` entry, in column order.

    Reuses :func:`_metric_facets` (the same flat-dotted-key vs nested extraction
    the table cells use) so the detail view and the table agree on what is
    present. Unlike the table cell (rounded to two places for width), the detail
    shows the metric's **full-precision** ``rate``; the CI band is shown when
    present (omitted otherwise, mirroring :func:`_metric_cell`); an **absent**
    metric shows ``—`` so it stays distinct from a genuine ``0.0``.
    """
    lines: list[str] = []
    for dotted_key, label in METRIC_ORDER:
        lines.append(f"  {label}: {_format_detail_metric(record, dotted_key)}")
    return _section("metrics", lines)


def _format_detail_metric(record: RawRecord, dotted_key: str) -> str:
    """Full-precision ``rate [ci_low–ci_high] count/denominator`` or ``—``.

    A value-type facet (the persona similarity facets ``persona_lex_mean`` /
    ``persona_sem_mean`` — ``mean``/``denominator``; ``persona_lex_peak`` /
    ``persona_sem_peak`` — ``peak``/``denominator`` — each with no ``rate``)
    renders as ``~<value> (n=<denominator>)`` at full precision, mirroring the
    table cell's value form (no CI band — neither is a binomial rate).
    """
    facets = _metric_facets(record, dotted_key)
    if facets is None:
        return _ABSENT

    # Value-type metric (the persona ``*_mean`` / ``*_peak`` facets): a ``mean`` or
    # ``peak`` value with no ``rate``.
    value = facets.get("mean")
    if value is None:
        value = facets.get("peak")
    if value is not None and facets.get("rate") is None:
        denom = facets.get("denominator")
        if denom is None:
            return _ABSENT
        return f"~{repr(float(value))} (n={denom})"

    rate = facets.get("rate")
    count = facets.get("count")
    denom = facets.get("denominator")
    if rate is None or count is None or denom is None:
        return _ABSENT

    # Full precision in the detail view — do not truncate the rate (the table
    # rounds it for column width; here we show what the ledger recorded).
    head = repr(float(rate))
    ci_low = facets.get("ci_low")
    ci_high = facets.get("ci_high")
    if ci_low is not None and ci_high is not None:
        band = f" [{repr(float(ci_low))}{_CI_DASH}{repr(float(ci_high))}]"
    else:
        band = ""
    return f"{head}{band} {count}/{denom}"


def _render_notes_section(record: RawRecord) -> str:
    """The free-text note verbatim — newlines preserved, never collapsed.

    An absent or empty ``notes`` (the common ``notes: ''`` empty-but-present
    case) shows ``—``; otherwise the note is rendered exactly as stored, so a
    YAML literal-block multi-line note keeps its line breaks.
    """
    note = _dig(record, "notes")
    text = "" if note is None else str(note)
    body = text if text else _ABSENT
    return _section("notes", [body])
