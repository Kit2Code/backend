# grader-worker: the judge. isolate is built from the official source at a pinned tag and commit.
# (The compilers and the Python worker code are added with the sandbox work.)
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

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2 libseccomp2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=isolate-build /src/isolate/isolate /usr/local/bin/isolate
COPY --from=isolate-build /src/isolate/isolate-check-environment /usr/local/bin/isolate-check-environment
COPY judge/worker/isolate.conf /usr/local/etc/isolate
COPY judge/worker/entrypoint.sh /usr/local/bin/worker-entrypoint
RUN chmod 4755 /usr/local/bin/isolate \
    && install -d -m 0755 /var/local/lib/isolate

ENTRYPOINT ["/usr/local/bin/worker-entrypoint"]
# Placeholder until the worker loop exists: keep the container up so isolate can be exercised.
CMD ["sleep", "infinity"]
