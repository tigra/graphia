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
    "TableModel",
    "load_ledger",
    "build_table_model",
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
# §2.3). ``⚠`` is the dirty-working-copy marker column; the rest are the run's
# identifying facts.
_FIXED_COLUMNS: tuple[str, ...] = (
    "⚠",
    "Date",
    "Provider",
    "Large model",
    "Small model",
    "Games",
)

# The en-dash separating the two CI bounds in a metric cell (``[lo–hi]``) — a
# typographic dash, not a hyphen, matching the tech-spec cell format.
_CI_DASH = "–"

# Sentinel distinguishing "key absent" from a present ``None`` value, so
# :func:`_dig` can keep walking into a level that genuinely holds ``None``
# without mistaking it for a missing key.
_MISSING = object()


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


@dataclass(frozen=True, slots=True)
class TableModel:
    """The flattened ledger as four **index-parallel** lists.

    ``rows[i]``, ``search_blobs[i]`` and ``records[i]`` all describe the *same*
    run — index-parallelism is the contract the UI relies on to resolve a
    selected (or filtered) table row back to its raw record. ``columns`` is the
    shared header list every ``rows[i]`` aligns to.

    - ``columns`` — header labels (the fixed leading columns then one per
      :data:`METRIC_ORDER` entry).
    - ``rows`` — one list of **formatted plain-string cells** per run, aligned to
      ``columns`` (Rich/justification stays a UI concern).
    - ``search_blobs`` — one lowercased, searchable string per run (date,
      provider, both model ids, commit, branch, a ``dirty``/``clean`` keyword,
      and the full notes text), so a substring filter is a flat ``in`` test.
    - ``records`` — the raw :data:`RawRecord` backing each row, for the detail
      drill-down.
    """

    columns: list[str]
    rows: list[list[str]]
    search_blobs: list[str]
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

    for record in records:
        rows.append(_row_cells(record))
        search_blobs.append(_search_blob(record))

    return TableModel(
        columns=columns,
        rows=rows,
        search_blobs=search_blobs,
        records=list(records),
    )


def _row_cells(record: RawRecord) -> list[str]:
    """Format one record's fixed + metric cells (plain strings), in column order."""
    dirty = bool(_dig(record, "code.dirty", default=False))
    # Identity models prefer the effective ``settings`` values (what actually
    # ran, post-override); pre-provenance records have no ``settings`` block, so
    # they fall back to the ``provider`` ids (``settings.* ?? provider.*``).
    large_model = _dig(
        record, "settings.large_model", default=_dig(record, "provider.large_model", "")
    )
    small_model = _dig(
        record, "settings.small_model", default=_dig(record, "provider.small_model", "")
    )
    # Games: the effective ``settings.games`` when present, else the
    # pre-provenance ``run.games`` fallback.
    games = _dig(record, "settings.games", default=_dig(record, "run.games", ""))

    cells: list[str] = [
        "⚠" if dirty else "",
        _text(_dig(record, "run.date", "")),
        _text(_dig(record, "provider.name", "")),
        _text(large_model),
        _text(small_model),
        _text(games),
    ]
    cells.extend(_metric_cell(record, dotted_key) for dotted_key, _ in METRIC_ORDER)
    return cells


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
    code commit and branch, a ``dirty``/``clean`` keyword, and the **full notes**
    text — the facets the viewer's substring filter searches across. Model ids
    prefer the effective ``settings`` values with the ``provider`` fallback (same
    rule as the row cells), so a pre-provenance record still contributes its
    model ids to the blob. Everything is lowercased so the UI's per-keystroke
    ``query in blob`` test is case-insensitive.
    """
    dirty = bool(_dig(record, "code.dirty", default=False))
    parts = [
        _text(_dig(record, "run.date", "")),
        _text(_dig(record, "provider.name", "")),
        _text(
            _dig(
                record,
                "settings.large_model",
                default=_dig(record, "provider.large_model", ""),
            )
        ),
        _text(
            _dig(
                record,
                "settings.small_model",
                default=_dig(record, "provider.small_model", ""),
            )
        ),
        _text(_dig(record, "code.commit", "")),
        _text(_dig(record, "code.branch", "")),
        "dirty" if dirty else "clean",
        _text(_dig(record, "notes", "")),
    ]
    return " ".join(part for part in parts if part).lower()


def _text(value: Any) -> str:
    """Render a scalar as display text — ``""`` for ``None``, else ``str``.

    A ``None`` (a YAML ``null`` field, or a :func:`_dig` default) renders as the
    empty string rather than the literal ``"None"``; everything else is stringified.
    """
    return "" if value is None else str(value)
