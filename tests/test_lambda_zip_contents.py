"""career_consumer.zip build-output integration tests.

The Lambda runtime ships its own (older) boto3, and that snapshot is
missing the bedrock-agentcore batch record-write operations we need —
calling ``batch_create_memory_records`` on the runtime's boto3 raises
``AttributeError`` and the Lambda crashes before any record is written
(commit a7a5d38). Our fix vendors a current boto3 into the zip via
``infra/lambda/career_consumer/requirements.txt``. These tests guard the
build contract:

* The zip is actually built (``make build-lambdas`` ran).
* It vendors ``boto3``/``botocore`` packages.
* The vendored botocore's bedrock-agentcore service description carries
  the operations we call — locking the "Lambda boto3 has BatchCreate" claim
  to the actual shipped artifact, not whatever our local venv happens to
  have.
* It vendors the source files we share with ``src/graphia/``.

The whole module skips if the zip hasn't been built yet — local dev runs
that haven't touched the Lambda still pass ``pytest -q``. CI is expected
to run ``make build-lambdas`` before ``pytest``.
"""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

LAMBDA_ZIP = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "lambda"
    / ".build"
    / "career_consumer.zip"
)


@pytest.fixture(scope="module")
def zip_namelist() -> list[str]:
    """Names of every entry in the built career_consumer.zip.

    Skipping (not failing) when the zip is absent lets developers who
    haven't run ``make build-lambdas`` still get a clean ``pytest -q``.
    """
    if not LAMBDA_ZIP.exists():
        pytest.skip(
            f"{LAMBDA_ZIP} not built; run `make build-lambdas` first "
            "(or ignore this skip in dev)"
        )
    with zipfile.ZipFile(LAMBDA_ZIP, "r") as zf:
        return zf.namelist()


def test_zip_carries_lambda_handler(zip_namelist):
    assert "lambda_function.py" in zip_namelist


def test_zip_vendors_career_events_module(zip_namelist):
    """Slice-8.7 vendoring: ``career_events.py`` is copied from src/graphia/
    with its imports flattened. The Lambda's ``CareerEvent``/``build_summary``
    decoders depend on it."""
    assert "career_events.py" in zip_namelist


def test_zip_vendors_stats_store_module(zip_namelist):
    """Same as above for ``stats_store.py`` — the Lambda uses ``CareerStats``,
    ``fold``, ``_career_from_json``, ``_career_to_json`` from this module."""
    assert "stats_store.py" in zip_namelist


def test_zip_vendors_boto3_package(zip_namelist):
    """If ``boto3/__init__.py`` is absent, the Lambda resolves boto3 against
    the runtime's bundled snapshot — which lacks the bedrock-agentcore batch
    record-write APIs (commit a7a5d38). Lock the vendoring contract."""
    assert "boto3/__init__.py" in zip_namelist, (
        "career_consumer.zip must vendor boto3 — without it the Lambda "
        "falls back to the runtime's older snapshot, which lacks the "
        "bedrock-agentcore batch record-write APIs"
    )


def test_zip_vendors_botocore_package(zip_namelist):
    """boto3 depends on botocore at runtime; both must travel in the zip."""
    assert "botocore/__init__.py" in zip_namelist


def _load_bedrock_agentcore_service_spec(zip_namelist: list[str]) -> dict:
    """Load the vendored bedrock-agentcore data-plane service description.

    botocore gzip-compresses its service descriptions (``service-2.json.gz``)
    and namespaces them by API version under ``botocore/data/<service>/<version>/``.
    Decoding once here keeps the per-test assertions tight and protects the
    location/encoding contract in one place.
    """
    service_files = [
        n
        for n in zip_namelist
        if n.startswith("botocore/data/bedrock-agentcore/")
        and n.endswith("/service-2.json.gz")
    ]
    assert service_files, (
        "vendored botocore lacks a bedrock-agentcore service description; "
        "the data plane API surface is unknown to this Lambda"
    )
    with zipfile.ZipFile(LAMBDA_ZIP, "r") as zf:
        raw = zf.read(service_files[0])
    return json.loads(gzip.decompress(raw))


def test_vendored_botocore_describes_batch_create_memory_records(zip_namelist):
    """The vendored botocore must carry a bedrock-agentcore service
    description whose ``operations`` map includes the batch record-write
    operations the Lambda calls.

    This catches the exact class of bug that ate the live deploy:
    boto3 is technically vendored, but the version is too old to know
    about ``BatchCreateMemoryRecords``. Walking the JSON service
    description side-steps any import-cache / sys.path complications
    that come with re-importing boto3 from inside the zip mid-test.
    """
    spec = _load_bedrock_agentcore_service_spec(zip_namelist)
    operations = spec.get("operations") or {}
    for op_name in (
        "ListEvents",
        "ListMemoryRecords",
        "BatchCreateMemoryRecords",
        "BatchUpdateMemoryRecords",
        "CreateEvent",
    ):
        assert op_name in operations, (
            f"vendored bedrock-agentcore service description lacks {op_name}; "
            "upgrade boto3 in infra/lambda/career_consumer/requirements.txt"
        )


def test_vendored_botocore_list_events_input_includes_includepayloads(zip_namelist):
    """Locks the plural ``includePayloads`` against the vendored shape.

    The local-boto3 contract test catches it against the developer's venv;
    this one catches it against the artifact the Lambda actually loads,
    which can drift if requirements pinning slips backwards.
    """
    spec = _load_bedrock_agentcore_service_spec(zip_namelist)
    list_events_input_shape_ref = (
        spec["operations"]["ListEvents"]["input"]["shape"]
    )
    list_events_input = spec["shapes"][list_events_input_shape_ref]
    members = list_events_input.get("members") or {}
    assert "includePayloads" in members
    assert "includePayload" not in members
