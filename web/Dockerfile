# grader-web: the Django app. The same image runs as the public site and as the admin site,
# chosen with GRADER_ROLE. (The production stage with gunicorn and static files comes later.)
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

COPY --from=ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1 /uv /bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first (cached between code changes): the lockfile plus every workspace member's pyproject.
COPY pyproject.toml uv.lock ./
COPY web/pyproject.toml web/
COPY common/pyproject.toml common/
COPY judge/worker/pyproject.toml judge/worker/
RUN uv sync --frozen --no-dev --package grader-web --no-install-workspace

COPY common common
COPY web web
RUN uv sync --frozen --no-dev --package grader-web

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app
WORKDIR /app/web
EXPOSE 8000
ENTRYPOINT ["/app/web/docker-entrypoint.sh"]
