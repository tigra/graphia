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
    "load_ledger",
    "build_table_model",
    "render_detail",
    "row_matches_field",
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
    "Provider",
    "Large model",
    "Small model",
    "Games",
    "Wins (LA/M)",
    "Votes (LA/M)",
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
        _text(_dig(record, "provider.name", "")),
        _text(large_model),
        _text(small_model),
        _text(games),
        _outcomes_cell(record),
        _vote_activity_cell(record),
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
    """
    facets = _metric_facets(record, dotted_key)
    if facets is None:
        return ""

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
    code commit and branch, a ``dirty``/``clean`` keyword, the derived ``winner``
    keyword (the side that won the run's majority — :func:`_winner_keyword` —
    empty on a pre-013 record), and the **full notes** text — the facets the
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
    ``dirty``/``clean`` keyword derived from ``code.dirty``. ``winner`` carries the
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
    ``quality`` → ``outcomes`` → ``vote_activity`` → ``metrics`` → ``notes``
    (tech-spec 012 §2.5, extended by 013 §2.3 — the two game-dynamics blocks sit
    after ``quality`` and before ``metrics``, matching the record key order). The
    thin Textual ``DetailScreen`` (a later task) wraps this string in a scroller;
    **no Rich/Textual concern lives here**, mirroring the table model's
    plain-string contract.

    Defensive throughout: every field is read via :func:`_dig`, so a
    *pre-provenance* record (no ``code`` / ``settings`` blocks, no CI bands,
    game count under ``run.games``) renders without raising — an absent scalar
    shows as :data:`_ABSENT` (``—``) and a whole absent sub-block collapses to a
    single ``—`` line. A ``KeyError`` never escapes.

    What each section shows:

    - **run** — date, ``duration_seconds``, ``metrics_version``, and the
      ``run.games`` fallback count when present.
    - **code** — ``commit``, ``branch``, and the working-copy state spelled out
      as ``dirty`` / ``clean`` (the table only flags it with ``⚠``).
    - **provider** — ``name`` and the resolved model ids, then the
      shape-specific extras: ollama ``models`` digests + ``server_version``, or
      the bedrock ``note``.
    - **settings** — the effective resolved values incl. ``games``, plus
      ``metrics_version`` mirrored here for a like-for-like repeat.
    - **quality** — the run-quality counts.
    - **outcomes** — the win-rate by side (013 §2.1): ``games``, then
      ``law_abiding``/``mafia`` each with ``wins`` + **full-precision** ``rate`` +
      a ``[ci_low–ci_high]`` band (rate/band omitted on the ``games == 0`` path),
      then the bare ``draw``/``no_winner`` counts and the immutable ``note``
      caveat. A whole absent block (pre-013 record) collapses to one ``—`` line.
    - **vote_activity** — AI vote-initiation counts (013 §2.2): a ``by_side``
      sub-block (**both** sides always, the explicit-zero) and a ``by_day``
      sub-block (``day_N: n`` sorted by integer suffix, or a ``(none)`` line when
      empty so "present but no per-day activity" stays distinct from an absent
      block, which collapses to one ``—`` line).
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
        _render_metrics_section(record),
        _render_notes_section(record),
    ]
    return "\n\n".join(sections)


def _section(title: str, lines: list[str]) -> str:
    """Join a section header with its ``label: value`` lines (one block)."""
    return "\n".join([title, *lines])


def _field(label: str, value: Any) -> str:
    """One ``label: value`` line; an absent (``None``/blank) value shows ``—``."""
    text = _text(value)
    return f"  {label}: {text if text else _ABSENT}"


def _render_run_section(record: RawRecord) -> str:
    lines = [
        _field("date", _dig(record, "run.date")),
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


def _render_settings_section(record: RawRecord) -> str:
    settings = _dig(record, "settings")
    if not isinstance(settings, dict):
        return _section("settings", [f"  {_ABSENT}"])
    return _section(
        "settings",
        [
            _field("large_model", _dig(record, "settings.large_model")),
            _field("small_model", _dig(record, "settings.small_model")),
            _field("base_url", _dig(record, "settings.base_url")),
            _field("games", _dig(record, "settings.games")),
            _field("seed", _dig(record, "settings.seed")),
            _field("max_rounds", _dig(record, "settings.max_rounds")),
            # Spec-014 lineup, defensively dug — a pre-014 record (no
            # ``settings.lineup``) shows the ``—`` em-dash, no migration.
            _field("citizens", _dig(record, "settings.lineup.num_citizens")),
            _field("mafia", _dig(record, "settings.lineup.num_mafia")),
        ],
    )


def _render_quality_section(record: RawRecord) -> str:
    quality = _dig(record, "quality")
    if not isinstance(quality, dict):
        return _section("quality", [f"  {_ABSENT}"])
    return _section(
        "quality",
        [
            _field("games_attempted", _dig(record, "quality.games_attempted")),
            _field("games_completed", _dig(record, "quality.games_completed")),
            _field("games_failed_early", _dig(record, "quality.games_failed_early")),
            _field("duration_seconds", _dig(record, "quality.duration_seconds")),
        ],
    )


def _render_outcomes_section(record: RawRecord) -> str:
    """The ``outcomes`` block — win-rate by side (013 §2.1), or one ``—`` line.

    A whole **absent** block (any pre-013 record) collapses to a single ``—``
    line, mirroring :func:`_render_code_section`'s absent pattern. When present:
    ``games``, then ``law_abiding``/``mafia`` each as ``wins`` + **full-precision**
    ``rate`` + a ``[ci_low–ci_high]`` band (rate + band omitted on the
    ``games == 0`` path, where only ``wins`` is recorded), then the bare ``draw``
    / ``no_winner`` counts and the immutable ``note`` caveat. Every read is
    defensive (:func:`_dig`), so a malformed/partial block never raises.
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
    """Full-precision ``rate [ci_low–ci_high] count/denominator`` or ``—``."""
    facets = _metric_facets(record, dotted_key)
    if facets is None:
        return _ABSENT

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
