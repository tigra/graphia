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
# The Runtime is started under the AWS Distro for OpenTelemetry (ADOT)
# auto-instrumentation wrapper (``opentelemetry-instrument``) below, so the
# GenAI Observability console renders a navigable per-session trace tree
# instead of flat log entries. These OTEL_* vars are the image-side half of
# the configuration; AgentCore Runtime injects the exporter endpoint and the
# CloudWatch log-group headers into the container environment at launch, so
# nothing here hardcodes an endpoint.
#
#   AGENT_OBSERVABILITY_ENABLED  — activates the ADOT pipeline.
#   OTEL_PYTHON_DISTRO           — selects the AWS distro.
#   OTEL_PYTHON_CONFIGURATOR     — required for ADOT Python (AWS configurator).
#   OTEL_RESOURCE_ATTRIBUTES     — service.name identifies the Graphia Runtime
#                                  in the GenAI Observability dashboard.
#
# Per the AWS docs (bedrock-agentcore devguide, "Add observability to your
# AgentCore resources"), ``opentelemetry-instrument`` auto-loads every
# instrumentor whose target package is importable. With aws-opentelemetry-
# distro 0.17 that includes ``aws_langchain`` (LangGraph node/chain spans),
# ``botocore`` (the Bedrock model calls langchain-aws makes underneath
# ChatBedrockConverse), and ``aws_mcp`` + ``httpx`` (the outbound
# Gateway-fronted MCP tool calls) — the three span sources the per-session
# trace tree is built from. No extra instrumentor package is needed.
ENV AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator \
    OTEL_RESOURCE_ATTRIBUTES=service.name=graphia-runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src

EXPOSE 8080

# Start under the ADOT auto-instrumentation wrapper. The runtime module's
# ``app.run(host="0.0.0.0")`` is unchanged — ``opentelemetry-instrument`` only
# prepends the SDK to the Python path and patches the instrumented libraries
# before handing off to ``python -m graphia.runtime``.
CMD ["opentelemetry-instrument", "python", "-m", "graphia.runtime"]
