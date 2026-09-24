#!/usr/bin/env bash
# Lev agent API client. Never prints the token; it is passed to curl on stdin, not argv.
# Usage: lw.sh GET /api/agent/tasks[/<id>]   |   lw.sh POST /api/agent/tasks/<id>/{proposal,verify,dismiss} body.json
set -euo pipefail
method=${1:?GET or POST}; path=${2:?path}; body=${3:-}
url=${LEV_URL:-http://localhost:8080}
token=${LEV_AGENT_TOKEN:-}
if [ -z "$token" ]; then
  env_file=${LEV_ENV:-$(dirname "$0")/../../../.env}
  token=$(sed -n "s/^AGENT_TOKEN=['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}$/\1/p" "$env_file" 2>/dev/null || true)
fi
# On the central host: the token Lev generated on first start.
[ -n "$token" ] || token=$(docker exec lev-api-1 cat /run/lev/agent_token 2>/dev/null || true)
[ -n "$token" ] || { echo "No agent token: set LEV_AGENT_TOKEN (Lev → Sources → Copy agent token)" >&2; exit 2; }
case "$path" in /api/agent/*) ;; *) echo "Only /api/agent/* is allowed" >&2; exit 2;; esac
args=(-sS --fail-with-body -X "$method" -H @- "$url$path")
[ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data-binary "@$body")
# Limit the task list to this machine's incidents: LEV_SERVER_ID, else the source server's Vector .env.
# The central host has no such file and sees all servers.
: "${LEV_SERVER_ID:=$(sed -n "s/^VECTOR_SERVER_ID=['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}$/\1/p" "${LEV_VECTOR_ENV:-/opt/lev-vector/.env}" 2>/dev/null || true)}"
if [ "$path" = /api/agent/tasks ] && [ -n "${LEV_SERVER_ID:-}" ]; then args+=(-G --data-urlencode "server_id=$LEV_SERVER_ID"); fi
printf 'Authorization: Bearer %s\n' "$token" | curl "${args[@]}"
echo
