#!/bin/sh
set -eu
VECTOR_HASH=$(cat /run/lev/vector_hash)
export VECTOR_HASH
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
