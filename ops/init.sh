#!/bin/sh
# Generates the stack's internal secrets once and never overwrites them. Values passed in the
# environment (a .env from an earlier setup) are kept, so existing installs keep working.
set -eu
umask 077
cd /run/lev
put() { [ -s "$1" ] || printf %s "${2:-$(head -c 32 /dev/urandom | base64 | tr -d '\n=+/')}" > "$1"; }
put postgres_password "${POSTGRES_PASSWORD:-}"
put vector_password "${VECTOR_PASSWORD:-}"
put agent_token "${AGENT_TOKEN:-}"
# The ingest password is 32 random bytes, so bcrypt cost 4 loses nothing and keeps each push cheap to check.
# Read from stdin, not argv (visible in ps). Older installs' cost-14 hashes are replaced.
case "$(cat vector_hash 2>/dev/null)" in
    '$2a$04$'*) ;;
    *) { cat vector_password; echo; } | caddy hash-password --bcrypt-cost 4 > vector_hash ;;
esac
chown 65534:65534 ./* && chmod 400 ./*
echo 'Secrets ready.'
