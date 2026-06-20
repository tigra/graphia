"""Fail-fast Ollama preflight, run at boot before the Textual TUI starts.

When the player selects ``GRAPHIA_LLM_PROVIDER=ollama`` (local mode only),
we verify *before* Textual takes the screen that (a) the Ollama server is
reachable and (b) both configured models are actually installed. Failures
surface as ``SystemExit`` with a plain-language fix-it message — exactly the
same channel ``load_config()`` already uses for config errors — so the
player never sees a traceback or a half-started TUI.

Stdlib only (``urllib.request`` + ``json``): the preflight must not pull in
httpx/requests just for one GET.
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
