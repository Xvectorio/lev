FROM timberio/vector:0.51.1-debian
COPY vector.yaml local.vrl /etc/vector/
COPY --chmod=755 entrypoint.sh /usr/local/bin/lev-vector
RUN mkdir -p /etc/vector/watch.d
ENTRYPOINT ["lev-vector"]
