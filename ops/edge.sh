#!/bin/sh
set -eu
VECTOR_HASH=$(cat /run/lev/vector_hash)
export VECTOR_HASH
# Plain HTTP on every interface sends passwords, the session cookie, the ingest password and the agent token unencrypted.
case "${SITE_ADDRESS:-:80}" in :*|http://*)
    case "${BIND_ADDRESS:-0.0.0.0}" in 0.0.0.0|::|'')
        echo 'WARNING: Lev serves plain HTTP on all interfaces. Credentials cross the network unencrypted.' >&2
        echo 'WARNING: Set SITE_ADDRESS for HTTPS, or BIND_ADDRESS=127.0.0.1 if only this server needs access (see docs/guide.md).' >&2
    esac
esac
cfg=/etc/caddy/Caddyfile
# Opt-in DNS-01 via Cloudflare, for names Let's Encrypt can't reach over HTTP (LAN-only, no public 80/443).
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    cfg=/tmp/Caddyfile
    { printf '{\n\tacme_dns cloudflare {env.CLOUDFLARE_API_TOKEN}\n}\n'; cat /etc/caddy/Caddyfile; } > "$cfg"
fi
exec caddy run --config "$cfg" --adapter caddyfile
