# Multi-stage: dependencies resolve once, the runtime image carries no build tools.
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependency layer first, so a source change does not re-resolve the lockfile.
COPY pyproject.toml uv.lock ./
# The AI layer is an extra; without it the orchestrated engine cannot import.
RUN uv sync --frozen --no-install-project --no-dev --extra ai


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/data/hf-cache

# Non-root: the process writes only to its data directory.
RUN useradd --create-home --uid 10001 legallens

WORKDIR /app
COPY --from=deps --chown=legallens:legallens /app/.venv /app/.venv
COPY --chown=legallens:legallens app ./app
COPY --chown=legallens:legallens migrations ./migrations
COPY --chown=legallens:legallens scripts ./scripts
COPY --chown=legallens:legallens alembic.ini pyproject.toml ./

RUN mkdir -p /app/data && chown -R legallens:legallens /app/data
USER legallens

VOLUME ["/app/data"]
EXPOSE 8000

# NFR-6: readiness reports a degraded dependency rather than failing outright.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# Migrations run at start so a fresh volume comes up usable.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.start:app --host 0.0.0.0 --port 8000"]
