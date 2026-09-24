#!/bin/sh
set -eu
# Source servers pass VECTOR_PASSWORD; the central stack reads the generated one.
[ -n "${VECTOR_PASSWORD:-}" ] || VECTOR_PASSWORD=$(cat /run/lev/vector_password)
export VECTOR_PASSWORD
exec vector --config /etc/vector/vector.yaml --config-dir /etc/vector/watch.d "$@"
