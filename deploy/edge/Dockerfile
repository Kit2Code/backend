# grader-edge: Caddy with our Caddyfile baked in (no config files on the host). Runs as an unprivileged
# user: every port is above 1024, and with auto_https off Caddy needs no certificates.
FROM caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f
COPY deploy/edge/Caddyfile /etc/caddy/Caddyfile
COPY --chmod=0755 deploy/edge/edge-start.sh /usr/local/bin/edge-start
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
    && caddy fmt --diff /etc/caddy/Caddyfile \
    && chown -R 65534:65534 /data /config
USER 65534:65534
EXPOSE 8080 8081 8082 8090
# Start-up checks (edge-start.sh), then Caddy.
ENTRYPOINT ["/usr/local/bin/edge-start"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
