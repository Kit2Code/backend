#!/bin/sh
# Start-up checks for the edge, then the command (Caddy).
# With GRADER_REQUIRE_HTTPS_UPSTREAMS=1 (the ZimaOS compose), a Google upstream that isn't https is
# refused: the token and key requests carry the app's Google client secret and the keys that
# sign-in trusts, and the photo request carries the user's photo address. The dev stack uses a
# plain-http stand-in for Google and leaves it off.
set -eu

if [ "${GRADER_REQUIRE_HTTPS_UPSTREAMS:-0}" = "1" ]; then
    for name in GRADER_GOOGLE_TOKEN_UPSTREAM GRADER_GOOGLE_CERTS_UPSTREAM GRADER_GOOGLE_PHOTO_UPSTREAM; do
        value=$(printenv "$name" || true)
        case "$value" in
            "" | https://*) ;;
            *)
                echo "edge: $name must be an https:// address (got: $value)" >&2
                exit 1
                ;;
        esac
    done
fi

exec "$@"
