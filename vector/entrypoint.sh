#!/bin/sh
set -eu
# Source servers pass VECTOR_PASSWORD; the central stack reads the generated one.
[ -n "${VECTOR_PASSWORD:-}" ] || VECTOR_PASSWORD=$(cat /run/lev/vector_password)
export VECTOR_PASSWORD
[ ! -f /etc/vector/watch.d/local.vrl ] || export LEV_LOCAL_VRL=/etc/vector/watch.d/local.vrl
exec vector --config /etc/vector/vector.yaml --config-dir /etc/vector/watch.d "$@"
