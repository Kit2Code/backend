# grader-web: the Django app. The same image runs as the public site and as the admin site,
# chosen with GRADER_ROLE. Stages:
#   translations  compiles web/locale/*/django.po into .mo files (gettext stays in this stage)
#   base          dependencies + code, built with uv
#   dev           the dev stack's image (runserver comes from the compose file)
#   static        runs collectstatic once, with throw-away build settings
#   prod          (the default) only Python, the virtualenv, the code and the collected files;
#                 runs gunicorn as uid 10001
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS translations
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY web/locale /locale
RUN for po in /locale/*/LC_MESSAGES/django.po; do msgfmt --check -o "${po%.po}.mo" "$po"; done

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS base

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
COPY --from=translations /locale web/locale
RUN uv sync --frozen --no-dev --package grader-web

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app/web
EXPOSE 8000
ENTRYPOINT ["/app/web/docker-entrypoint.sh"]

FROM base AS dev
USER app

FROM base AS static
# collectstatic only reads settings; these values exist for this one command and are thrown away.
RUN mkdir /tmp/build-secrets \
    && python -c "import secrets; print(secrets.token_urlsafe(48))" > /tmp/build-secrets/django_secret_key \
    && echo unused > /tmp/build-secrets/db_password \
    && GRADER_ROLE=public PUBLIC_BASE_URL=http://build.invalid GRADER_DB_HOST=build.invalid \
       GRADER_SECRETS_DIR=/tmp/build-secrets python manage.py collectstatic --noinput -v 0 \
    && rm -rf /tmp/build-secrets

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS prod
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=base /opt/venv /opt/venv
COPY --from=base /app/common /app/common
COPY --from=static /app/web /app/web
USER app
WORKDIR /app/web
EXPOSE 8000
ENTRYPOINT ["/app/web/docker-entrypoint.sh"]
CMD ["serve"]
