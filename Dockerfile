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
# The navigable per-session trace tree needs three things, and a hand-built
# image (Graphia writes its own Dockerfile rather than using the agentcore
# starter-toolkit) must supply all three:
#
# 1. An OpenTelemetry SDK + TracerProvider. The ``bedrock-agentcore`` SDK
#    does NOT create one — ``runtime/app.py`` explicitly comments that "in
#    the managed runtime ADOT sets up the TracerProvider before __init__
#    runs". ADOT = the ``opentelemetry-instrument`` launch wrapper below
#    (with ``aws-opentelemetry-distro``). Without it every span lands on
#    OpenTelemetry's no-op default provider and is exported nowhere — the
#    cause of the flat trajectory on image 89deed3.
# 2. The framework instrumentor for the LangGraph node / ChatBedrockConverse
#    model spans — ``openinference-instrumentation-langchain``, activated
#    programmatically in ``graphia.runtime.observability``'s
#    ``configure_runtime_observability()``. Generic ADOT does not instrument
#    LangGraph; there is no ``aws_langchain`` auto-instrumentor.
# 3. A per-invocation root span (``runtime_invocation_span`` in the entry
#    point) so node/model/tool spans nest into one tree.
#
# OTEL_RESOURCE_ATTRIBUTES is deliberately NOT set here: the managed runtime
# injects it carrying ``cloud.resource_id`` (the runtime ARN the SDK parses
# at app.py:_parse_runtime_arn). Hardcoding it would clobber that value.
# See ``infra/terraform/RESEARCH.md`` §12.
ENV AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src

EXPOSE 8080

# Run under the ADOT auto-instrumentation wrapper — it configures the OTEL
# SDK / TracerProvider the bedrock-agentcore SDK and the in-code LangChain
# instrumentor both depend on. The runtime module's app.run(host=0.0.0.0)
# is unchanged; opentelemetry-instrument only sets up the SDK before exec.
CMD ["opentelemetry-instrument", "python", "-m", "graphia.runtime"]
