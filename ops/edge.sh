#!/bin/sh
set -eu
VECTOR_HASH=$(cat /run/lev/vector_hash)
export VECTOR_HASH
cfg=/etc/caddy/Caddyfile
# Opt-in DNS-01 via Cloudflare, for names Let's Encrypt can't reach over HTTP (LAN-only, no public 80/443).
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    cfg=/tmp/Caddyfile
    { printf '{\n\tacme_dns cloudflare {env.CLOUDFLARE_API_TOKEN}\n}\n'; cat /etc/caddy/Caddyfile; } > "$cfg"
fi
exec caddy run --config "$cfg" --adapter caddyfile
