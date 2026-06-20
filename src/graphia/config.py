"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
# Recommended model defaults — smoke-verified 2026-06-12 via
# `make ollama-smoke`: qwen3-coder:30b (large) + qwen2.5:3b (small) ran a
# full scripted game with zero structured-output failures. The provisional
# qwen2.5:7b large candidate was rejected — it answers in prose instead of
# making the tool call (40/40 DayAction failures).
_DEFAULT_OLLAMA_LARGE_MODEL = "qwen3-coder:30b"
_DEFAULT_OLLAMA_SMALL_MODEL = "qwen2.5:3b"

_DEFAULT_NUM_CITIZENS = 5
_DEFAULT_NUM_MAFIA = 2
# Whole-game runaway safeguard (spec 023). A Mafia game thins out to a winner
# on its own — at the largest allowed table the longest natural game is only
# ~10 Days — so this Day cap is NOT what ends a normal game; it exists purely
# to stop a stuck/looping ("runaway") game. Default 12 Days, just above the
# worst-case natural game, leaving reserve for a larger roster. Tunable via
# ``GRAPHIA_MAX_DAYS`` so prior behaviour is reproducible for A/B (ADR 011).
_DEFAULT_MAX_DAYS = 12
# Documented ceiling on total table size (Citizens + Mafiosos). Chosen so a
# full Day round (total + 1 messages) stays well inside ``_CONTEXT_WINDOW``,
# the small model's one-shot name request stays modest, and per-game vote-poll
# cost/tokens stay bounded. It is a single deliberate cap, trivially raised.
_MAX_TABLE_SIZE = 12


@dataclass(slots=True, frozen=True)
class GraphiaConfig:
    bearer_token: str | None
    aws_region: str
    log_file: Path
    checkpoint_dir: Path
    stats_file: Path
    human_role: str | None
    remote_mode: bool
    runtime_invocation_url: str | None
    memory_id: str | None
    career_memory_id: str | None
    # Gateway plumbing (Slice 7 sub-task 3). Both fields are normally
    # only populated inside the Runtime container — Terraform sets
    # ``GRAPHIA_GATEWAY_ID`` on the Runtime resource's environment_variables
    # map. ``gateway_url`` is a convenience derivation for clients that want
    # to point a streamable-HTTP MCP client at the Gateway without
    # reassembling the URL pattern; local-mode developers can also set
    # ``GRAPHIA_GATEWAY_URL`` directly for ad-hoc Gateway probing.
    gateway_id: str | None
    gateway_url: str | None
    # CloudWatch log group carrying the deployed Runtime's logs/traces
    # (Slice 8). Read from ``GRAPHIA_LOG_GROUP``; ``make wire-env`` pulls it
    # from the ``cloudwatch_log_group`` Terraform output. Normally only set
    # for remote play — the remote-mode crash modal uses it to point the
    # player at the failed session's CloudWatch coordinates. ``None`` is
    # tolerated: the modal degrades to showing the filter expression alone.
    cloudwatch_log_group: str | None
    # Self-managed career memory strategy id and its namespace (Slice 6,
    # remote only). Plumbed from ``terraform output``; ``stats_strategy_id``
    # is ``None`` in local mode where career stats live in ``stats_file``.
    stats_strategy_id: str | None
    stats_namespace: str | None
    # LLM provider selection (spec 010). ``bedrock`` keeps the existing
    # cloud path; ``ollama`` targets a locally running Ollama server and is
    # local-mode only (contradiction with ``remote_mode`` is rejected below).
    # Defaulted so tests constructing the config directly stay valid.
    llm_provider: str = "bedrock"
    ollama_base_url: str = _DEFAULT_OLLAMA_BASE_URL
    ollama_large_model: str = _DEFAULT_OLLAMA_LARGE_MODEL
    ollama_small_model: str = _DEFAULT_OLLAMA_SMALL_MODEL
    # Configurable lineup (spec 014). Whole-table counts including the human;
    # validated in ``load_config`` before the TUI starts. Defaulted so tests
    # constructing the config directly stay valid.
    num_citizens: int = _DEFAULT_NUM_CITIZENS
    num_mafia: int = _DEFAULT_NUM_MAFIA
    # Whole-game runaway safeguard in Days (spec 023). The single day-denominated
    # game-length limit, applied identically in real play and measured runs; a
    # game reaching it is flagged runaway/unresolved, never a real result.
    # Tunable via ``GRAPHIA_MAX_DAYS`` (default 12). Defaulted so tests
    # constructing the config directly stay valid.
    max_days: int = _DEFAULT_MAX_DAYS
    # End-of-round Day recap (spec 018). The ablation off-switch: on by
    # default; an explicit falsy ``GRAPHIA_DAY_ROUND_RECAP`` plays the Day
    # exactly as before. Defaulted so tests constructing the config directly
    # stay valid.
    day_round_recap_enabled: bool = True
    # Recap-aware AI reasoning (spec 019, retrofitted under ADR 011). The
    # ablation off-switch: on by default; an explicit falsy
    # ``GRAPHIA_RECAP_AWARE_REASONING`` reverts the AI Day-speech and vote
    # prompts to their pre-019 form (no standings block injected). Mirrors the
    # spec-018 ``day_round_recap_enabled`` precedent exactly. Defaulted so tests
    # constructing the config directly stay valid.
    recap_aware_reasoning_enabled: bool = True
    # Role-specific Day guidance (spec 024, ADR 011). The ablation off-switch: on
    # by default; an explicit falsy ``GRAPHIA_ROLE_GUIDANCE`` reverts the AI
    # Day-speech and vote prompts to their pre-024 form (no role-matched closing
    # directive injected at the prompt tail). Mirrors the spec-019
    # ``recap_aware_reasoning_enabled`` precedent exactly. Defaulted so tests
    # constructing the config directly stay valid.
    role_guidance_enabled: bool = True


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def _env_flag(name: str, *, default: bool) -> bool:
    """Default-aware boolean env flag.

    Returns ``default`` when the variable is unset or blank; otherwise returns
    whether the stripped/lowercased value is truthy. Unlike ``_env_truthy``
    (which is default-off), this supports a flag that is on by default and
    requires an explicit falsy value (``0``/``false``/``no``/``off``) to disable.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY


def _parse_count(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise SystemExit(f"{name} must be a whole number (got {raw!r}).")


def load_config() -> GraphiaConfig:
    # Legacy / workshop-token path: if set, hand it through. Otherwise leave
    # None and rely on boto3's default credential chain (AWS_PROFILE / SSO /
    # instance role) at the call site.
    bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or None

    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    log_file = Path(os.environ.get("GRAPHIA_LOG_FILE", "./.graphia/graphia.log"))
    checkpoint_dir = Path(
        os.environ.get("GRAPHIA_CHECKPOINT_DIR", "./.graphia/checkpoints")
    )
    stats_file = Path(os.environ.get("GRAPHIA_STATS_FILE", "./.graphia/career.json"))

    remote_mode = _env_truthy("GRAPHIA_REMOTE")
    day_round_recap_enabled = _env_flag("GRAPHIA_DAY_ROUND_RECAP", default=True)
    recap_aware_reasoning_enabled = _env_flag(
        "GRAPHIA_RECAP_AWARE_REASONING", default=True
    )
    role_guidance_enabled = _env_flag("GRAPHIA_ROLE_GUIDANCE", default=True)
    runtime_invocation_url = os.environ.get("GRAPHIA_RUNTIME_URL") or None
    memory_id = os.environ.get("GRAPHIA_MEMORY_ID") or None
    career_memory_id = os.environ.get("GRAPHIA_CAREER_MEMORY_ID") or None
    cloudwatch_log_group = os.environ.get("GRAPHIA_LOG_GROUP") or None
    stats_strategy_id = os.environ.get("GRAPHIA_STATS_STRATEGY_ID")
    stats_namespace = os.environ.get(
        "GRAPHIA_STATS_NAMESPACE", "/career/human-career/"
    )
    gateway_id = os.environ.get("GRAPHIA_GATEWAY_ID") or None
    # Prefer an explicitly supplied URL (useful for local-mode probing
    # against a deployed Gateway) but derive it from the id + region when
    # the Runtime container is configured with only ``GRAPHIA_GATEWAY_ID``.
    gateway_url = os.environ.get("GRAPHIA_GATEWAY_URL") or None
    if gateway_url is None and gateway_id is not None:
        gateway_url = (
            f"https://{gateway_id}.gateway.bedrock-agentcore."
            f"{aws_region}.amazonaws.com/mcp"
        )

    role_raw = os.environ.get("GRAPHIA_ROLE")
    if role_raw is None or not role_raw.strip():
        human_role: str | None = None
    else:
        match role_raw.strip().lower():
            case "mafia":
                human_role = "mafia"
            case "law-abiding":
                human_role = "law_abiding"
            case _:
                raise SystemExit(
                    f"GRAPHIA_ROLE must be 'mafia' or 'law-abiding' (got {role_raw!r})."
                )

    provider_raw = os.environ.get("GRAPHIA_LLM_PROVIDER", "bedrock")
    match provider_raw.strip().lower():
        case "" | "bedrock":
            llm_provider = "bedrock"
        case "ollama":
            llm_provider = "ollama"
        case _:
            raise SystemExit(
                "GRAPHIA_LLM_PROVIDER must be 'bedrock' or 'ollama' "
                f"(got {provider_raw!r})."
            )

    ollama_base_url = os.environ.get(
        "GRAPHIA_OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL
    )
    ollama_large_model = os.environ.get(
        "GRAPHIA_OLLAMA_LARGE_MODEL", _DEFAULT_OLLAMA_LARGE_MODEL
    )
    ollama_small_model = os.environ.get(
        "GRAPHIA_OLLAMA_SMALL_MODEL", _DEFAULT_OLLAMA_SMALL_MODEL
    )

    if remote_mode and llm_provider == "ollama":
        raise SystemExit(
            "GRAPHIA_LLM_PROVIDER=ollama can't be combined with remote mode "
            "(--remote / GRAPHIA_REMOTE=1): Ollama runs on your machine and "
            "can't be reached from the deployed Runtime. Use the cloud "
            "provider for --remote, or drop --remote to play locally on "
            "Ollama."
        )

    if remote_mode and not runtime_invocation_url:
        raise SystemExit(
            "Remote mode requested (--remote / GRAPHIA_REMOTE=1) but "
            "GRAPHIA_RUNTIME_URL is not set. Run "
            "`terraform output runtime_invocation_url` from infra/terraform/ "
            "and add the value to .env as `GRAPHIA_RUNTIME_URL=...`."
        )

    # Offline gate (functional-spec 010 §2.2): an Ollama game must complete
    # "without reaching any cloud service". A wire-env'd ``.env`` carries a
    # deployed stack's Memory/Gateway ids, and the diary/career factories
    # gate on those ids alone — a live local game died on
    # ``UnauthorizedSSOTokenError`` mid-session because career events still
    # targeted AgentCore Memory. Blanking the cloud-store ids here, at the
    # single config choke point, drops stats/diaries to their local/no-op
    # implementations exactly as on a fresh machine (local career stats in
    # ``stats_file`` are unaffected). The remote+ollama contradiction guard
    # above already ensures ollama implies local mode.
    if llm_provider == "ollama":
        memory_id = None
        career_memory_id = None
        gateway_id = None
        gateway_url = None
        stats_strategy_id = None

    # Lineup validation (spec 014 §2.1). Whole-table counts, human included;
    # every invalid lineup fails fast here with the broken rule named, before
    # the TUI (or an eval) starts. Negative/zero counts are caught by the
    # ``< 1`` guards since e.g. ``int("-3")`` parses then fails ``< 1``.
    num_citizens = _parse_count("GRAPHIA_NUM_CITIZENS", _DEFAULT_NUM_CITIZENS)
    num_mafia = _parse_count("GRAPHIA_NUM_MAFIA", _DEFAULT_NUM_MAFIA)

    # Runaway Day cap (spec 023). Parsed like the other counts; defaults to 12
    # Days. A value < 1 is nonsensical (no game has zero Days), so reject it.
    max_days = _parse_count("GRAPHIA_MAX_DAYS", _DEFAULT_MAX_DAYS)
    if max_days < 1:
        raise SystemExit(
            f"GRAPHIA_MAX_DAYS must be at least 1 (got {max_days})."
        )

    if num_mafia < 1:
        raise SystemExit(
            "GRAPHIA_NUM_MAFIA must be at least 1 — a game with no Mafiosos "
            f"is already over (nobody to find). Got {num_mafia}."
        )
    if num_citizens < 1:
        raise SystemExit(
            f"GRAPHIA_NUM_CITIZENS must be at least 1 (got {num_citizens})."
        )
    if num_mafia >= num_citizens:
        raise SystemExit(
            f"GRAPHIA_NUM_MAFIA ({num_mafia}) must be strictly fewer than "
            f"GRAPHIA_NUM_CITIZENS ({num_citizens}) — otherwise the Mafia "
            "start at or above the parity that wins them the game before it "
            "begins."
        )
    total = num_citizens + num_mafia
    if total > _MAX_TABLE_SIZE:
        raise SystemExit(
            f"Table too large: {num_citizens} Citizens + {num_mafia} "
            f"Mafiosos = {total} players exceeds the maximum of "
            f"{_MAX_TABLE_SIZE}."
        )

    return GraphiaConfig(
        bearer_token=bearer_token,
        aws_region=aws_region,
        log_file=log_file,
        checkpoint_dir=checkpoint_dir,
        stats_file=stats_file,
        human_role=human_role,
        remote_mode=remote_mode,
        runtime_invocation_url=runtime_invocation_url,
        memory_id=memory_id,
        career_memory_id=career_memory_id,
        gateway_id=gateway_id,
        gateway_url=gateway_url,
        cloudwatch_log_group=cloudwatch_log_group,
        stats_strategy_id=stats_strategy_id,
        stats_namespace=stats_namespace,
        llm_provider=llm_provider,
        ollama_base_url=ollama_base_url,
        ollama_large_model=ollama_large_model,
        ollama_small_model=ollama_small_model,
        num_citizens=num_citizens,
        num_mafia=num_mafia,
        max_days=max_days,
        day_round_recap_enabled=day_round_recap_enabled,
        recap_aware_reasoning_enabled=recap_aware_reasoning_enabled,
        role_guidance_enabled=role_guidance_enabled,
    )
