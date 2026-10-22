# grader-ops: the one-shot `secrets-init` and the long-running `backup-loop`.
# Based on the same Postgres image as the database, so pg_dump/pg_restore match the server version.
# The two commands are small Python programs (deploy/ops/grader_ops/) that call those tools.
FROM postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
RUN apk add --no-cache python3
COPY deploy/ops/grader_ops /opt/grader_ops/grader_ops
RUN printf '#!/bin/sh\nexec python3 -m grader_ops.secrets_init "$@"\n' > /usr/local/bin/secrets-init \
    && printf '#!/bin/sh\nexec python3 -m grader_ops.backup_loop "$@"\n' > /usr/local/bin/backup-loop \
    && chmod 0755 /usr/local/bin/secrets-init /usr/local/bin/backup-loop
ENV PYTHONPATH=/opt/grader_ops PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Not Postgres's own entrypoint: these commands run as given.
ENTRYPOINT []
CMD ["backup-loop"]
