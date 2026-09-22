"""Render a recorded OpenTelemetry trace tree from CloudWatch as text.

AgentCore's in-app spans do **not** live in the X-Ray segment store —
``xray batch-get-traces`` returns only the platform's
``AgentCore.Runtime.Invoke`` root (one segment, ~50ms) and none of the
LangGraph / Bedrock children. The full tree is delivered by Transaction
Search into the ``aws/spans`` log group, which is what this tool reads.

One remote game is many traces: the Runtime is invoked once per
interrupt-resume round, and each invocation is its own trace. They are tied
together by the AgentCore ``session.id`` carried on every root span, so the
default view lists sessions and you drill into one.

Usage:

    uv run python -m graphia.tools.show_trace                  # newest session
    uv run python -m graphia.tools.show_trace --list           # all sessions
    uv run python -m graphia.tools.show_trace --session <sid>
    uv run python -m graphia.tools.show_trace --trace-id <id>  # either id format
    uv run python -m graphia.tools.show_trace --days 2 --lines 200

Requires a live SSO session (``AWS_PROFILE``, read from ``.env``) and
Transaction Search enabled in the region (``make enable-transaction-search``).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from dotenv import load_dotenv

SPANS_LOG_GROUP = "aws/spans"
PLATFORM_ROOT_SPAN = "AgentCore.Runtime.Invoke"
QUERY_TIMEOUT_S = 90
QUERY_POLL_S = 2
NAME_WIDTH = 58

_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _run_insights(client: Any, query: str, start_s: int, end_s: int) -> list[dict[str, str]]:
    """Run one Logs Insights query over aws/spans and return its rows as dicts."""
    try:
        query_id = client.start_query(
            logGroupNames=[SPANS_LOG_GROUP],
            startTime=start_s,
            endTime=end_s,
            queryString=query,
            limit=10000,
        )["queryId"]
    except client.exceptions.ResourceNotFoundException:
        print(
            f"error: log group {SPANS_LOG_GROUP!r} does not exist in this region.\n"
            "Transaction Search is probably off — run `make enable-transaction-search`.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    deadline = time.monotonic() + QUERY_TIMEOUT_S
    while True:
        resp = client.get_query_results(queryId=query_id)
        status = resp["status"]
        if status == "Complete":
            return [{f["field"]: f["value"] for f in row} for row in resp["results"]]
        if status in ("Failed", "Cancelled", "Timeout"):
            print(f"error: Logs Insights query {status}", file=sys.stderr)
            raise SystemExit(1)
        if time.monotonic() >= deadline:
            print(f"error: query still {status} after {QUERY_TIMEOUT_S}s", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(QUERY_POLL_S)


def to_xray_id(trace_id: str) -> str:
    """Convert a 32-hex OTel trace id to X-Ray's ``1-<epoch>-<rest>`` form.

    The two consoles disagree on format: aws/spans records the raw OTel id,
    while the X-Ray console and API want the first 8 hex digits split out as
    the timestamp. Pasting one where the other is expected yields
    "incorrect id format", which reads like the trace is missing.
    """
    raw = trace_id.replace("-", "")
    if len(raw) != 32:
        return trace_id
    return f"1-{raw[:8]}-{raw[8:]}"


def from_xray_id(trace_id: str) -> str:
    """Normalise either id format to the 32-hex form aws/spans stores."""
    if trace_id.startswith("1-"):
        return trace_id[2:].replace("-", "")
    return trace_id


def fetch_root_spans(client: Any, start_s: int, end_s: int) -> list[dict[str, str]]:
    """One row per trace: timestamp, trace id, session id."""
    return _run_insights(
        client,
        "fields @timestamp, traceId, attributes.session.id as sid, durationNano as ns"
        f' | filter name = "{PLATFORM_ROOT_SPAN}"'
        " | sort @timestamp desc",
        start_s,
        end_s,
    )


def fetch_spans(client: Any, trace_id: str, start_s: int, end_s: int) -> list[dict]:
    """Every span record belonging to one trace, parsed from JSON."""
    rows = _run_insights(
        client,
        f'fields @message | filter traceId = "{trace_id}"',
        start_s,
        end_s,
    )
    spans = []
    for row in rows:
        message = row.get("@message")
        if not message:
            continue
        try:
            spans.append(json.loads(message))
        except json.JSONDecodeError:
            continue
    return spans


def _scope_of(span: dict) -> str:
    scope = span.get("scope") or {}
    if isinstance(scope, dict):
        return scope.get("name") or "platform"
    return str(scope) or "platform"


def _error_flag(span: dict) -> str:
    """Mark only genuinely failed spans.

    The CloudWatch exporter writes ``{"code": "OK"}`` or ``{"code": "UNSET"}``
    — not the ``STATUS_CODE_*`` spelling used elsewhere in OTel — so matching
    the wrong vocabulary flags every span as an error.
    """
    code = (span.get("status") or {}).get("code") or ""
    return " !" if code.upper().removeprefix("STATUS_CODE_") not in ("", "OK", "UNSET") else ""


def _short_scope(name: str) -> str:
    """`openinference.instrumentation.langchain` -> `openinference/langchain`."""
    parts = [p for p in name.split(".") if p != "instrumentation"]
    if len(parts) <= 2:
        return "/".join(parts)
    return f"{parts[0]}/{parts[-1]}"


def render_tree(spans: list[dict], max_lines: int) -> None:
    """Print the spans as an indented tree, deepest-last within each parent."""
    by_id = {s["spanId"]: s for s in spans}
    children: dict[str, list[dict]] = collections.defaultdict(list)
    roots: list[dict] = []
    for span in spans:
        parent = span.get("parentSpanId")
        if parent and parent in by_id:
            children[parent].append(span)
        else:
            roots.append(span)
    for kids in children.values():
        kids.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))
    roots.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))

    printed = 0
    hidden = 0
    depths: collections.Counter[int] = collections.Counter()

    def walk(span: dict, depth: int) -> None:
        nonlocal printed, hidden
        depths[depth] += 1
        if printed < max_lines:
            ms = int(span.get("durationNano", 0)) / 1e6
            # Indent inside the name column so the duration column stays put
            # however deep the tree goes.
            label = "  " * depth + span.get("name", "?")
            if len(label) > NAME_WIDTH:
                label = label[: NAME_WIDTH - 1] + "…"
            scope = _short_scope(_scope_of(span))
            print(f"  {label:<{NAME_WIDTH}} {ms:9.1f}ms  {_DIM}{scope}{_RESET}{_error_flag(span)}")
            printed += 1
        else:
            hidden += 1
        for kid in children[span["spanId"]]:
            walk(kid, depth + 1)

    for root in roots:
        walk(root, 0)

    if hidden:
        print(f"  {_DIM}… {hidden} more span(s) — raise with --lines{_RESET}")
    print()
    scopes = collections.Counter(_scope_of(s) for s in spans)
    print(f"  {len(spans)} spans, depth {max(depths) + 1 if depths else 0}, {len(roots)} root(s)")
    for name, count in scopes.most_common():
        print(f"    {count:>5}  {name}")


def _print_session_table(rows: list[dict[str, str]]) -> None:
    sessions: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in rows:
        sessions[row.get("sid") or "<no session.id>"].append(row)
    print(f"{'session.id':<50} {'traces':>6}  first trace         last trace")
    print(f"{'-' * 50} {'-' * 6}  {'-' * 19} {'-' * 19}")
    for sid, group in sorted(sessions.items(), key=lambda kv: kv[1][0]["@timestamp"], reverse=True):
        stamps = sorted(r["@timestamp"] for r in group)
        print(f"{sid:<50} {len(group):>6}  {stamps[0][:19]}  {stamps[-1][:19]}")
    print(f"\n{_DIM}Timestamps are UTC, as CloudWatch records them.{_RESET}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="show-trace", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--session", help="Render every trace in this AgentCore session.")
    parser.add_argument("--trace-id", help="Render one trace (raw 32-hex or X-Ray 1-… form).")
    parser.add_argument("--list", action="store_true", help="List sessions and exit.")
    parser.add_argument(
        "--days", type=float, default=30.0,
        help="How far back to search. Default 30 — the aws/spans retention window.",
    )
    parser.add_argument("--lines", type=int, default=60, help="Max tree lines per trace (default 60).")
    parser.add_argument("--region", help="AWS region (defaults to AWS_REGION env or us-east-1).")
    args = parser.parse_args(argv)

    load_dotenv()
    region = args.region or os.environ.get("AWS_REGION") or "us-east-1"
    client = boto3.client("logs", region_name=region)

    end_s = int(time.time())
    start_s = end_s - int(args.days * 86400)

    if args.trace_id:
        trace_ids = [from_xray_id(args.trace_id)]
        session = None
    else:
        roots = fetch_root_spans(client, start_s, end_s)
        if not roots:
            print(
                f"No traces in {SPANS_LOG_GROUP} over the last {args.days:g} day(s).\n"
                "Play a remote game (`make play-remote`) or run `make verify-observability`.",
                file=sys.stderr,
            )
            return 1
        if args.list:
            _print_session_table(roots)
            return 0
        session = args.session or roots[0].get("sid")
        trace_ids = [r["traceId"] for r in roots if r.get("sid") == session]
        if not trace_ids:
            print(f"error: no traces for session {session!r}", file=sys.stderr)
            _print_session_table(roots)
            return 2

    print(f"region  : {region}")
    if session:
        print(f"session : {session}")
    print(f"traces  : {len(trace_ids)}\n")

    for i, trace_id in enumerate(reversed(trace_ids), start=1):
        spans = fetch_spans(client, trace_id, start_s, end_s)
        print(f"{_BOLD}── trace {i}/{len(trace_ids)}  {trace_id}{_RESET}")
        print(f"   {_DIM}x-ray id: {to_xray_id(trace_id)}{_RESET}")
        if not spans:
            print("   (no span records — outside the --days window?)\n")
            continue
        render_tree(spans, args.lines)
        print()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"{_DIM}Read from CloudWatch log group {SPANS_LOG_GROUP} at {stamp}.")
    print(
        "Graphical views (set the console time range to cover the trace — the "
        "default last-3-hours window is empty unless a game just ran):"
    )
    print(
        f"  waterfall  CloudWatch > GenAI Observability > Bedrock AgentCore > Sessions\n"
        f"             https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#gen-ai-observability/agent-core"
    )
    print(
        f"  trace map  CloudWatch > X-Ray traces > Trace Map\n"
        f"             https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#xray:service-map/map{_RESET}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
