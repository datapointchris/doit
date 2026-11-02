# doit's service. Built from the repository root, because the service runs the
# root package's view code: api/ depends on it by path. Dockerfile.dockerignore
# beside this file keeps everything else out of the context.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

# The root package takes its version from git, and the context carries no
# .git. The release passes the version it built from.
ARG DOIT_VERSION=0.0.0
ENV UV_DYNAMIC_VERSIONING_BYPASS=${DOIT_VERSION}

WORKDIR /app

# Dependencies first, in their own layer, so a source change does not reinstall
# them. The root pyproject is here because the lock resolves api/'s path
# dependency through it.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=api/uv.lock,target=api/uv.lock \
    --mount=type=bind,source=api/pyproject.toml,target=api/pyproject.toml \
    uv sync --directory api --locked --no-install-project --no-install-package doit --no-dev

COPY README.md LICENSE ./
COPY src/ src/
COPY api/pyproject.toml api/uv.lock api/
COPY api/src/ api/src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --directory api --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm

# The database lives on a volume mounted at /var/lib/doit-api, writable by this
# uid, so the image and the volume agree on who may write it.
RUN groupadd --system --gid 10001 doit && useradd --system --uid 10001 --gid doit --no-create-home doit \
    && mkdir -p /var/lib/doit-api && chown doit:doit /var/lib/doit-api

COPY --from=build --chown=doit:doit /app/api/.venv /app/api/.venv

ENV PATH="/app/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DOIT_API_DATABASE=/var/lib/doit-api/doit.db

USER doit

EXPOSE 8080

# /health answers with no auth and no dependency, so a failing probe means the
# process itself is wedged.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]

CMD ["doit-api"]
