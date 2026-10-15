# syntax=docker/dockerfile:1
# Versions are pinned by digest; Renovate bumps them.
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

# Frontend: typecheck, unit tests and the bundle; a failing check stops the build. Install scripts stay off
# (only esbuild has one, and it finds its binary without it). The bundle is the same for every platform, so this
# stage runs natively on the build machine, also when the image is for another architecture.
FROM --platform=$BUILDPLATFORM node:24-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY ui/ ./
RUN npm run check && npm test && MTDL_UI_OUT=/ui/dist npm run build

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_FROZEN=1 \
    PATH=/opt/venv/bin:$PATH
# ffmpeg only muxes local files; tini (started by the entrypoint after the rights are dropped) is PID 1 and reaps what
# hook scripts leave behind. Versions pinned, Renovate bumps them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg=7:7.1.5-0+deb13u1 tini=0.19.0-3+b8 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

# Test stage: dependencies incl. dev tools, source via PYTHONPATH. Running the image runs every check.
# The slow test against the real film list: docker run --rm -v "$PWD/data:/app/data:ro" <image> pytest -m full
FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-install-project
ENV PYTHONPATH=/app/src
COPY src ./src
COPY --from=ui /ui/dist ./src/mtdl_web/ui
COPY test ./test
COPY tools ./tools
COPY website/src/data ./website/src/data
COPY compose.yaml compose.unraid.yaml .env.example ./
COPY docker/entrypoint.sh ./docker/
CMD ["sh", "-c", "ruff check src test tools && ruff format --check src test tools && mypy && pytest -q"]

# Application image. uv's cache stays in a build cache, not in the image.
FROM base AS app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev --no-install-project
ENV PYTHONPATH=/app/src \
    MTDL_CONFIG_DIR=/config \
    MTDL_DOWNLOAD_DIR=/downloads
COPY src ./src
COPY --from=ui /ui/dist ./src/mtdl_web/ui
COPY docker/entrypoint.sh /usr/local/bin/mtdl-entrypoint
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=6s --start-period=60s CMD ["python", "-m", "mtdl_web.healthcheck"]
ENTRYPOINT ["mtdl-entrypoint"]
CMD ["python", "-m", "mtdl_web"]
