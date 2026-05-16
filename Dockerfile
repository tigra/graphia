FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# --- AgentCore Observability (CR 003) ----------------------------------
# Graphia's game engine runs as a Bedrock AgentCore **Runtime-hosted**
# workload. AgentCore auto-instruments Runtime-hosted containers — it
# injects the OpenTelemetry SDK + OTLP exporter and ships the emitted spans
# to CloudWatch as the ``TRACES`` stream — so the image must NOT run under
# the ``opentelemetry-instrument`` launch wrapper, and must NOT set the
# manual ``OTEL_*`` / ``AGENT_OBSERVABILITY_ENABLED`` env block: that recipe
# is for *non-Runtime-hosted* agents and conflicts with the platform
# instrumentation (it suppressed all in-app telemetry on image 4f164f3).
# See ``infra/terraform/RESEARCH.md`` §12.
#
# The framework spans the navigable per-session trace tree is built from
# (LangGraph node execution + per-turn ChatBedrockConverse model calls) come
# from an explicit instrumentor — ``openinference-instrumentation-langchain``
# — activated programmatically in ``graphia.runtime.observability``'s
# ``configure_runtime_observability()``. Generic ADOT does not instrument
# LangGraph/LangChain; there is no ``aws_langchain`` auto-instrumentor.
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src

EXPOSE 8080

# Runtime-hosted recipe: run the module directly. AgentCore's platform
# auto-instrumentation wraps the process; the in-code LangChain instrumentor
# (loaded by ``configure_runtime_observability()``) emits the framework spans.
CMD ["python", "-m", "graphia.runtime"]
