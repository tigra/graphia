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
# The Runtime image runs under the ADOT ``opentelemetry-instrument`` launch
# wrapper (CMD below). The wrapper is load-bearing: the ``bedrock-agentcore``
# SDK does not create an OpenTelemetry TracerProvider itself — ``runtime/
# app.py`` notes that "in the managed runtime ADOT sets up the TracerProvider
# before __init__ runs". The wrapper also auto-loads the LangChain GenAI
# instrumentor (the ``langchain`` entry point resolves to openinference's
# ``LangChainInstrumentor``), which produces the graph-node / ChatBedrockConverse
# span tree; ``graphia.runtime.observability`` opens the per-invocation root
# span those spans nest under.
#
# The ADOT ``OTEL_*`` environment (``AGENT_OBSERVABILITY_ENABLED``,
# ``OTEL_PYTHON_DISTRO``, ``OTEL_PYTHON_CONFIGURATOR``, ``OTEL_RESOURCE_ATTRIBUTES``,
# the OTLP exporter endpoint) is injected by the managed AgentCore Runtime for
# a Runtime-hosted agent — it is deliberately NOT hardcoded here. Setting it in
# the image is redundant and risks clobbering the platform values (notably
# ``OTEL_RESOURCE_ATTRIBUTES``, which carries the runtime ARN the SDK parses).
# See ``infra/terraform/RESEARCH.md`` §12.

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src

EXPOSE 8080

# Run under the ADOT auto-instrumentation wrapper — it configures the OTEL
# SDK / TracerProvider the bedrock-agentcore SDK and the in-code LangChain
# instrumentor both depend on. The runtime module's app.run(host=0.0.0.0)
# is unchanged; opentelemetry-instrument only sets up the SDK before exec.
CMD ["opentelemetry-instrument", "python", "-m", "graphia.runtime"]
