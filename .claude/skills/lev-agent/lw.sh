#!/usr/bin/env bash
# Lev agent API client. Never prints the token; it is passed to curl on stdin, not argv.
# Usage: lw.sh GET /api/agent/tasks[/<id>]   |   lw.sh POST /api/agent/tasks/<id>/{proposal,verify,dismiss,note} body.json
#        lw.sh url    (the Lev base URL it talks to, for approval links)
set -euo pipefail
method=${1:?GET, POST or url}; path=${2:-}; body=${3:-}
# On a source server the Vector config (Docker or native install) holds the central URL and this server's ID.
vector_env=${LEV_VECTOR_ENV:-/opt/lev-vector/.env}
[ -r "$vector_env" ] || vector_env=/etc/default/vector
vget() { sed -n "s/^$1=['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}$/\1/p" "$vector_env" 2>/dev/null || true; }
url=${LEV_URL:-$(vget VECTOR_ENDPOINT)}; url=${url:-http://localhost:8080}; url=${url%/}
[ "$method" = url ] && { echo "$url"; exit 0; }
[ -n "$path" ] || { echo "Usage: lw.sh GET|POST /api/agent/... [body.json]" >&2; exit 2; }
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
# Limit the task list to this machine's incidents. The central host has no Vector config and sees all servers.
: "${LEV_SERVER_ID:=$(vget VECTOR_SERVER_ID)}"
if [ "$path" = /api/agent/tasks ] && [ -n "${LEV_SERVER_ID:-}" ]; then args+=(-G --data-urlencode "server_id=$LEV_SERVER_ID"); fi
printf 'Authorization: Bearer %s\n' "$token" | curl "${args[@]}"
echo
