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
import urllib.error
import urllib.request

from graphia.config import GraphiaConfig

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
