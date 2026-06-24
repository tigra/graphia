"""Fail-fast provider preflights, run at boot before the Textual TUI starts.

When the player selects ``GRAPHIA_LLM_PROVIDER=ollama`` (local mode only),
we verify *before* Textual takes the screen that (a) the Ollama server is
reachable and (b) both configured models are actually installed.

When the player selects ``GRAPHIA_LLM_PROVIDER=bedrock-claude`` (spec 035),
:func:`run_claude_preflight` verifies — before the TUI takes the screen — that
AWS credentials resolve, the chosen Claude model is reachable, and the region
is right, mapping the boto3/Bedrock ``AccessDenied`` / ``UnrecognizedClient`` /
``ValidationException`` error families to plain-language fixes.

Both surface failures as ``SystemExit`` with a plain-language fix-it message —
exactly the same channel ``load_config()`` already uses for config errors — so
the player never sees a traceback or a half-started TUI.

The Ollama path is stdlib-only (``urllib.request`` + ``json``); the Claude path
lazy-imports boto3/botocore so ``import graphia.preflight`` stays free of an
AWS dependency on the Ollama/Nova paths.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from graphia.config import GraphiaConfig

logger = logging.getLogger(__name__)

# Generous enough for a cold local server, short enough that a missing
# server fails the boot promptly.
_PREFLIGHT_TIMEOUT_SECONDS = 3.0


def _fetch_installed_models(base_url: str, timeout: float) -> list[str]:
    """Return the names of models installed on the Ollama server.

    Hits Ollama's native ``GET /api/tags`` endpoint, which answers
    ``{"models": [{"name": "qwen2.5:7b", ...}, ...]}``. Raises ``OSError``
    (which covers ``urllib.error.URLError`` and socket timeouts) when the
    server can't be reached. Kept as a tiny seam so tests can stub the HTTP
    round-trip without a live server.
    """
    url = base_url.rstrip("/") + "/api/tags"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    return [
        name
        for model in payload.get("models", [])
        if isinstance(name := model.get("name"), str)
    ]


def _model_installed(configured: str, installed: list[str]) -> bool:
    """Decide whether a configured model name matches an installed one.

    Tag-matching rule: ``ollama pull`` always stores models under an
    explicit tag — ``ollama pull qwen2.5:7b`` installs ``qwen2.5:7b``,
    while the tagless ``ollama pull qwen2.5`` installs ``qwen2.5:latest``.
    ``/api/tags`` therefore reports fully-tagged names only. So:

    - configured name carries a tag (``qwen2.5:7b``) → require an exact
      match, because tags are distinct model variants (7b vs 3b);
    - configured name is tagless (``qwen2.5``) → accept any installed tag
      of that model (``qwen2.5:latest``, ``qwen2.5:7b``, ...), mirroring
      how Ollama itself resolves a tagless reference.
    """
    if ":" in configured:
        return configured in installed
    return any(name.split(":", 1)[0] == configured for name in installed)


def run_ollama_preflight(config: GraphiaConfig) -> None:
    """Verify the Ollama server is up and the configured models exist.

    No-op unless ``llm_provider == "ollama"`` in local mode — Bedrock and
    remote play have their own credential/connectivity stories. Raises
    ``SystemExit`` with an actionable message on any failure.
    """
    if config.llm_provider != "ollama" or config.remote_mode:
        return

    base_url = config.ollama_base_url
    try:
        installed = _fetch_installed_models(base_url, _PREFLIGHT_TIMEOUT_SECONDS)
    except (OSError, ValueError) as exc:
        # OSError covers connection refused / DNS / timeout via urllib;
        # ValueError covers a non-JSON response from something that isn't
        # actually Ollama listening on that port.
        raise SystemExit(
            f"Couldn't reach Ollama at {base_url}. Is it running? "
            "Start it with: ollama serve"
        ) from exc

    # Report *all* missing models at once so the player doesn't pull one,
    # relaunch, and only then discover the other is missing too.
    missing = [
        name
        for name in dict.fromkeys(  # de-dupe while preserving order
            (config.ollama_large_model, config.ollama_small_model)
        )
        if not _model_installed(name, installed)
    ]
    if missing:
        raise SystemExit(
            "\n".join(
                f"The model '{name}' isn't installed. "
                f"Pull it with: ollama pull {name}"
                for name in missing
            )
        )

    # Spec 025 Route-A operator support: warn (never raise) if the large model's
    # effective context looks too small to hold the configured window's token
    # budget. The token-budget cap makes overflow impossible regardless, so this
    # is at worst a "fuller window not delivered" heads-up, never a failure.
    warn_if_ollama_context_too_small(config, base_url)


def _fetch_model_context_length(
    base_url: str, model: str, timeout: float
) -> int | None:
    """Best-effort read of a model's context length via Ollama ``/api/show``.

    Returns the ``model_info["<arch>.context_length"]`` value (the model's
    declared context, e.g. ``llama.context_length`` / ``qwen3.context_length``)
    when discoverable, else ``None``. This is the only context signal Ollama's
    HTTP surface exposes per loaded model; the server's *effective* ``num_ctx``
    (set via ``OLLAMA_CONTEXT_LENGTH``) is not separately reported, so this is a
    conservative proxy — paired with the never-overflow token-budget cap, a
    best-effort signal is all the startup check needs.

    Swallows everything (network, JSON, shape): a context check must never break
    the boot (spec 025 "never raises"). Returns ``None`` on any difficulty so
    the caller stays quiet rather than warning on a signal it couldn't read.
    """
    url = base_url.rstrip("/") + "/api/show"
    try:
        body = json.dumps({"model": model}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    model_info = payload.get("model_info")
    if not isinstance(model_info, dict):
        return None
    for key, value in model_info.items():
        if isinstance(key, str) and key.endswith(".context_length"):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def warn_if_ollama_context_too_small(
    config: GraphiaConfig, base_url: str | None = None
) -> None:
    """Log a warning if the Ollama large model's context is below the budget.

    Route-A belt-and-braces (spec 025 §2): the fuller window is delivered by the
    operator's server-side ``OLLAMA_CONTEXT_LENGTH``; a fresh/forgetful server
    reverting to Ollama's tiny ~4K default would silently fail to deliver it.
    This check reads the large model's declared context (``/api/show``) and logs
    a warning when it is below ``config.context_token_budget``, pointing the
    operator at ``OLLAMA_CONTEXT_LENGTH``. It NEVER raises — the token-budget cap
    already guarantees the prompt can't overflow, so a too-small context is a
    "fuller window not delivered" condition, not a truncated-instructions one.

    No-op for Bedrock / remote (whose context far exceeds the window) and a
    silent no-op when the signal can't be read.
    """
    if config.llm_provider != "ollama" or config.remote_mode:
        return
    resolved_base = base_url if base_url is not None else config.ollama_base_url
    context_length = _fetch_model_context_length(
        resolved_base, config.ollama_large_model, _PREFLIGHT_TIMEOUT_SECONDS
    )
    if context_length is None:
        # Couldn't read the signal — stay quiet rather than cry wolf.
        return
    if context_length < config.context_token_budget:
        logger.warning(
            "Ollama large model %r reports a context length of %d tokens, "
            "below the configured discussion-window budget of %d. The fuller "
            "multi-day window may not be fully delivered (the oldest history is "
            "trimmed to fit). Set OLLAMA_CONTEXT_LENGTH=32768 before "
            "`ollama serve` to give the model the full window.",
            config.ollama_large_model,
            context_length,
            config.context_token_budget,
        )


# ===========================================================================
# Claude (Bedrock) boot preflight (spec 035, Slice 2).
#
# Mirrors ``run_ollama_preflight``'s shape and channel exactly: a no-op unless
# the relevant provider is selected, a single cheap probe before the TUI takes
# the screen, and a plain-string ``SystemExit`` (message-to-stderr, exit 1, no
# traceback) on any failure. Where the Ollama preflight checks a local HTTP
# server, this one checks the cloud: do AWS credentials resolve, is the chosen
# Claude model reachable, is the region right — the three failure modes
# functional-spec 035 §2.5 calls out.
# ===========================================================================

# Bedrock error *codes* (``ClientError.response["Error"]["Code"]``) grouped by
# the plain-language fix they map to. These are the representative members of
# the families the tech-spec names (``UnrecognizedClient`` / ``AccessDenied`` /
# ``ValidationException``); the mapping is by code so a faked client can drive
# each branch deterministically in the offline tests.
_CLAUDE_CREDENTIAL_ERROR_CODES = frozenset(
    {
        "UnrecognizedClientException",
        "InvalidSignatureException",
        "ExpiredTokenException",
        "ExpiredToken",
        "InvalidClientTokenId",
        "AuthFailure",
    }
)
_CLAUDE_ACCESS_ERROR_CODES = frozenset(
    {
        "AccessDeniedException",
        "AccessDenied",
    }
)
_CLAUDE_REGION_OR_MODEL_ERROR_CODES = frozenset(
    {
        "ValidationException",
        "ResourceNotFoundException",
    }
)

# Substrings of boto3/botocore credential-resolution exception class names that
# mean "no usable credentials were found / the SSO session expired" — these are
# raised *before* any HTTP call (so they aren't ``ClientError``s), most often on
# an expired SSO login. Matched by class name so we don't have to import every
# botocore exception type just to isinstance-check it.
_CLAUDE_CRED_RESOLUTION_EXC_NAMES = frozenset(
    {
        "NoCredentialsError",
        "PartialCredentialsError",
        "UnauthorizedSSOTokenError",
        "SSOTokenLoadError",
        "TokenRetrievalError",
        "CredentialRetrievalError",
        "RefreshWithMFAUnsupportedError",
    }
)

# Minimal probe body: a single-word prompt capped at one output token. Enough
# to exercise the full credentials → region → model-access path on the real
# Converse endpoint, with negligible cost; the offline tests fake the probe
# seam so no live call is ever made from the suite.
_CLAUDE_PROBE_TIMEOUT_SECONDS = 10.0

_CREDENTIAL_FIX = (
    "Your AWS credentials are missing or expired. Refresh them — e.g. "
    "`aws sso login --profile <your-profile>` (or set AWS_PROFILE / "
    "AWS_BEARER_TOKEN_BEDROCK) — then relaunch."
)
_ACCESS_FIX = (
    "Your AWS account can reach Bedrock but is not allowed to invoke the "
    "Claude model '{model}'. Enable access to Anthropic Claude models in the "
    "Bedrock console (Model access) for region {region}, and confirm your "
    "IAM principal has bedrock:InvokeModel on that model, then relaunch."
)
_REGION_OR_MODEL_FIX = (
    "Bedrock rejected the Claude model id '{model}' in region {region}. "
    "Check that the id (or the us. inference profile) is correct and "
    "available in that region — set AWS_REGION, or override the tier with "
    "GRAPHIA_LARGE_MODEL / GRAPHIA_SMALL_MODEL — then relaunch."
)
_GENERIC_FIX = (
    "Couldn't reach the Claude model '{model}' on Bedrock in region {region}: "
    "{detail}. Check your AWS credentials, model access, and region, then "
    "relaunch."
)


def _probe_claude_access(config: GraphiaConfig) -> None:
    """Make one minimal Bedrock Converse call to verify the Claude path.

    The single seam the offline tests fake (a stand-in that raises a chosen
    boto3 error, or returns cleanly). Lazy-imports boto3 so importing this
    module stays AWS-free on the Nova/Ollama paths. Builds a ``bedrock-runtime``
    client in ``config.aws_region`` and issues a one-token ``converse`` against
    the configured large-tier model id — exercising credentials, region, and
    model access in a single negligible-cost call. Returns ``None`` on success;
    any boto3/botocore exception propagates to the caller for mapping.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "bedrock-runtime",
        region_name=config.aws_region,
        config=BotoConfig(
            connect_timeout=_CLAUDE_PROBE_TIMEOUT_SECONDS,
            read_timeout=_CLAUDE_PROBE_TIMEOUT_SECONDS,
            retries={"max_attempts": 1},
        ),
    )
    client.converse(
        modelId=config.large_model,
        messages=[{"role": "user", "content": [{"text": "ping"}]}],
        inferenceConfig={"maxTokens": 1},
    )


def _claude_failure_message(exc: Exception, config: GraphiaConfig) -> str:
    """Map a boto3/botocore exception to a plain-language, actionable fix.

    Branches on the Bedrock error *code* for ``ClientError``s and on the
    exception *class name* for credential-resolution errors (raised before any
    HTTP call). Anything unrecognised falls through to a generic — but still
    plain, no-stack-trace — message that still names the model and region.
    """
    model = config.large_model
    region = config.aws_region

    # Credential-resolution failures (no usable creds / expired SSO) are raised
    # before any request, so they are not ``ClientError``s — classify by class
    # name to avoid importing every botocore exception type.
    if type(exc).__name__ in _CLAUDE_CRED_RESOLUTION_EXC_NAMES:
        return _CREDENTIAL_FIX

    code = _error_code(exc)
    if code in _CLAUDE_CREDENTIAL_ERROR_CODES:
        return _CREDENTIAL_FIX
    if code in _CLAUDE_ACCESS_ERROR_CODES:
        return _ACCESS_FIX.format(model=model, region=region)
    if code in _CLAUDE_REGION_OR_MODEL_ERROR_CODES:
        return _REGION_OR_MODEL_FIX.format(model=model, region=region)

    return _GENERIC_FIX.format(model=model, region=region, detail=str(exc))


def _error_code(exc: Exception) -> str | None:
    """Best-effort extraction of a botocore ``ClientError`` error code.

    Reads ``exc.response["Error"]["Code"]`` (the shape every ``ClientError``
    carries) without importing botocore — so the mapping works on a faked
    ``ClientError`` in the offline suite as well as the real one. Returns
    ``None`` for exceptions that carry no such structure.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = error.get("Code")
            if isinstance(code, str):
                return code
    return None


def run_claude_preflight(config: GraphiaConfig) -> None:
    """Verify the Claude (Bedrock) path is reachable before the TUI starts.

    No-op unless ``llm_provider == "bedrock-claude"`` in local mode — Nova and
    Ollama have their own stories, and the deployed runtime authenticates via
    its own role (the deployed spike, Slice 3, proves that path). On any
    boto3/Bedrock failure, raises ``SystemExit`` with a plain-language,
    actionable message (no stack trace), mapping the credential / model-access /
    region error families per functional-spec 035 §2.5. The underlying
    exception is chained (``from exc``) for log forensics, but it is the
    string-coded ``SystemExit`` that propagates.
    """
    if config.llm_provider != "bedrock-claude" or config.remote_mode:
        return

    try:
        _probe_claude_access(config)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — map *any* boto3 failure to a plain message
        raise SystemExit(_claude_failure_message(exc, config)) from exc
