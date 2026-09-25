#!/bin/sh
# Installs Lev's Vector as a native systemd service, for source servers without Docker.
# Rerun to upgrade; /etc/default/vector and /etc/vector/watch.d are kept.
set -eu
LEV_VERSION=${LEV_VERSION:-latest}
[ "$LEV_VERSION" = latest ] && ref=main || ref=v$LEV_VERSION
src=${LEV_SRC:-https://raw.githubusercontent.com/Xvectorio/lev/$ref/vector}
[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for f in Dockerfile vector.yaml local.vrl entrypoint.sh .env.example; do
  curl -fsSL -o "$tmp/$f" "$src/$f"
done

# Same Vector version as the lev-vector image.
v=$(sed -n 's/^FROM timberio\/vector:\([0-9.]*\)-.*/\1/p' "$tmp/Dockerfile")
if ! vector --version 2>/dev/null | grep -q "^vector $v "; then
  pkg=https://packages.timber.io/vector/$v
  if command -v dpkg >/dev/null; then
    curl -fsSL -o "$tmp/vector.deb" "$pkg/vector_$v-1_$(dpkg --print-architecture).deb"
    dpkg -i --force-confold "$tmp/vector.deb"
  else
    curl -fsSL -o "$tmp/vector.rpm" "$pkg/vector-$v-1.$(uname -m).rpm"
    rpm -U --oldpackage --replacepkgs "$tmp/vector.rpm"
  fi
fi

install -m 644 "$tmp/vector.yaml" "$tmp/local.vrl" /etc/vector/
install -m 755 "$tmp/entrypoint.sh" /usr/local/bin/lev-vector
mkdir -p /etc/vector/watch.d /host/var
# vector.yaml and watch files use the container's /host/var/log paths; the link keeps them identical here.
[ -e /host/var/log ] || ln -s /var/log /host/var/log
if ! grep -q '^VECTOR_ENDPOINT=' /etc/default/vector 2>/dev/null; then
  sed -e '/^JOURNAL_GID=/d' -e 's/^VECTOR_CONTAINER_GLOB=.*/VECTOR_CONTAINER_GLOB=SELECT_CONTAINER_ID/' \
    "$tmp/.env.example" > /etc/default/vector
fi
chmod 600 /etc/default/vector
mkdir -p /etc/systemd/system/vector.service.d
cat > /etc/systemd/system/vector.service.d/lev.conf <<'EOF'
[Service]
ExecStartPre=
ExecStart=
ExecStart=/usr/local/bin/lev-vector
ExecReload=
ExecReload=/bin/kill -HUP $MAINPID
EOF
systemctl daemon-reload
if systemctl is-active -q vector; then
  systemctl restart vector
  echo "Vector $v updated and restarted."
else
  echo "Vector $v installed. Set the values in /etc/default/vector, then: systemctl enable --now vector"
fi
