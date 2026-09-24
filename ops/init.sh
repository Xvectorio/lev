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
[ -s vector_hash ] || caddy hash-password --plaintext "$(cat vector_password)" > vector_hash
chown 65534:65534 ./* && chmod 400 ./*
echo 'Secrets ready.'
