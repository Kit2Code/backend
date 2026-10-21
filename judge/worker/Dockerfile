# grader-worker: the judge. isolate is built from the official source at a pinned tag and commit;
# the compilers match CMS v1.5.1's own Dockerfile (Ubuntu 24.04 build-essential, python3.12, zip).
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac AS isolate-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates git libcap-dev libseccomp-dev \
    && rm -rf /var/lib/apt/lists/*

ARG ISOLATE_TAG=v2.7
ARG ISOLATE_COMMIT=8f185bb37f3f23e29b33b0c7727c91c13429abe3
RUN git clone --depth 1 --branch "$ISOLATE_TAG" https://github.com/ioi/isolate.git /src/isolate \
    && cd /src/isolate \
    && test "$(git rev-parse HEAD)" = "$ISOLATE_COMMIT" \
    && make isolate

FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

# libcap2/libseccomp2: isolate. build-essential, python3 (/usr/bin/python3), python3.12, zip: the
# contestant languages (CMS's set). python3.12-venv: the worker's own virtualenv.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libcap2 libseccomp2 build-essential python3 python3.12 python3.12-venv zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Dependencies first (cached between code changes).
COPY pyproject.toml uv.lock ./
COPY web/pyproject.toml web/
COPY common/pyproject.toml common/
COPY judge/worker/pyproject.toml judge/worker/
RUN uv sync --frozen --no-dev --package grader-worker --no-install-workspace

COPY common common
COPY judge/worker judge/worker
RUN uv sync --frozen --no-dev --package grader-worker

COPY --from=isolate-build /src/isolate/isolate /usr/local/bin/isolate
COPY --from=isolate-build /src/isolate/isolate-check-environment /usr/local/bin/isolate-check-environment
COPY judge/worker/isolate.base /usr/local/etc/isolate.base
COPY judge/worker/vendor/testlib/testlib.h /usr/local/share/grader/testlib.h
# The hostile programs, for the self-test that runs on the real host.
COPY fixtures/hostile /opt/grader/hostile
COPY judge/worker/entrypoint.sh /usr/local/bin/worker-entrypoint
# The worker runs as root, so isolate needs no setuid bit (one less setuid binary a box could try).
RUN chmod 0755 /usr/local/bin/isolate \
    && install -d -m 0755 /var/local/lib/isolate \
    && cp /usr/local/etc/isolate.base /usr/local/etc/isolate

ENTRYPOINT ["/usr/local/bin/worker-entrypoint"]
# Placeholder until the worker loop exists: keep the container up so the judge can be exercised.
CMD ["sleep", "infinity"]
