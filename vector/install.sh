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
for f in Dockerfile packages.sha256 vector.yaml local.vrl entrypoint.sh .env.example; do
  curl -fsSL -o "$tmp/$f" "$src/$f"
done

# Same Vector version as the lev-vector image.
v=$(sed -n 's/^FROM timberio\/vector:\([0-9.]*\)-.*/\1/p' "$tmp/Dockerfile")
# The package runs as root: check it against the checksum pinned in Lev's repo, not one from the package host.
fetch() {
  curl -fsSL -o "$tmp/$1" "$pkg/$1"
  sum=$(grep "  $1\$" "$tmp/packages.sha256" | cut -d' ' -f1)
  [ -n "$sum" ] || { echo "No pinned checksum for $1 in packages.sha256." >&2; exit 1; }
  echo "$sum  $tmp/$1" | sha256sum -c - >/dev/null || { echo "Checksum mismatch for $1; not installing." >&2; exit 1; }
}
if ! vector --version 2>/dev/null | grep -q "^vector $v "; then
  pkg=https://packages.timber.io/vector/$v
  if command -v dpkg >/dev/null; then
    deb=vector_$v-1_$(dpkg --print-architecture).deb
    fetch "$deb"
    dpkg -i --force-confold "$tmp/$deb"
  else
    rpm=vector-$v-1.$(uname -m).rpm
    fetch "$rpm"
    rpm -U --oldpackage --replacepkgs "$tmp/$rpm"
  fi
fi

install -m 644 "$tmp/vector.yaml" "$tmp/local.vrl" /etc/vector/
install -m 755 "$tmp/entrypoint.sh" /usr/local/bin/lev-vector
mkdir -p /etc/vector/watch.d /host/var
# vector.yaml and watch files use the container's /host/var/log paths; the link keeps them identical here.
[ -e /host/var/log ] || ln -s /var/log /host/var/log
if ! grep -q '^VECTOR_ENDPOINT=' /etc/default/vector 2>/dev/null; then
  { printf '%s\n' \
      '# Read by the vector systemd service (native install). After setting the values below, start it:' \
      '#   systemctl enable --now vector    # starts Vector now and at every boot' \
      '# After a later change to this file: systemctl restart vector. Logs: journalctl -u vector' \
      ''
    sed -e '/^JOURNAL_GID=/d' -e '/^# JOURNAL_GID/d' \
      "$tmp/.env.example"
  } > /etc/default/vector
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
