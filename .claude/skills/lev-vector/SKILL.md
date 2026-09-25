---
name: lev-vector
description: Install and configure Vector on a server so it ships logs to Lev, and add log sources when asked to "watch <thing>" (a service, app, container or log file). Discovers every log the thing writes (journald, Docker, files), adds only what isn't already collected, tests the parsing and confirms collection. Also explains how to format log messages in your own apps so Lev levels, groups and triages them well. Use for "install vector", "connect this server to lev", "watch nginx/postgres/my app", "add logs for X", "why are X's logs missing in lev", "how should my app log for lev", "log format for lev".
---

# Lev Vector

Vector on each server reads logs, normalizes and redacts them, and pushes **warn/error/fatal plus a 60s heartbeat** to Lev (Caddy → Loki). It runs in Docker. The only exception is a source server without Docker: there it runs as a native systemd service installed by Lev's `vector-install.sh` (see **Native**). Never install Vector on the host any other way.

**Lev repo:** the central Lev checkout. If this skill is invoked from another project, paths below (`vector/`, `compose.yaml`, `compose.tools.yaml`, `backend/…`) are relative to that repo, and central-host commands run there.

## Compatibility contract (never break)

- `vector.yaml` is **shared and identical on every server**. Don't edit it per server. Its `normalize` → `multiline` → `exceptions` → `local` → `relevant` → `loki` chain sets the labels (`project_id, server_id, host, service, environment`), severity, redaction and heartbeat that Lev depends on. Resolution of incidents needs the heartbeat.
- Per-server additions go **only** in `watch.d/<thing>.yaml` plus read-only volume mounts in the compose file. `normalize` takes input from `"watch_*"`, so a `watch_<thing>` transform is picked up automatically.
- In a watch file, name the source `src_<thing>` (never `watch_…`, or it would be ingested twice) and the remap `watch_<thing>`. The remap may set only:
  - `.lev_service`: the service label. Keep it short and stable: `nginx`, `payments-api`.
  - `.lev_level`: the level, only for formats the shared parser misses.
  - `.message`: extra redaction only.
- To silence or promote lines this server already collects (e.g. firewall blocks in the journal), use `watch.d/local.vrl` (create it if missing, keep existing rules), never `vector.yaml` or the default `local.vrl` beside it (both are replaced on upgrades). It runs as the `local` remap after the shared levelling, so it sees `.message`, `.level`, `.service`, and may only set `.level` (only warn/error/fatal reach Lev) or `abort` to drop. Test it with `insert_at: local` / `extract_from: relevant` in a watch file's `tests:`. Without the file, a no-op default from the image is used.
- Never add labels (Loki cardinality), and never add a second sink, `docker.sock`, a `/` mount or write access.
- Never print `.env` or the ingestion password.

## Where things live

| | Central Lev host | Source server |
|---|---|---|
| Files | repo `vector/` | `compose.yaml`, `.env`, `watch.d/` (e.g. `/opt/lev-vector`); `vector.yaml` is in the `lev-vector` image |
| Compose | repo `compose.override.yaml` (untracked), service `vector` | `vector/compose.yaml`, with `--env-file .env` |
| Apply | `docker compose up -d vector` | `docker compose up -d` |
| Logs | `docker compose logs --since 2m vector` | same, in the `vector/` dir |

**Native** (source server without Docker, `systemctl cat vector` shows a `lev.conf` drop-in): settings in `/etc/default/vector`, watch files in `/etc/vector/watch.d/`, apply with `systemctl restart vector`, logs with `journalctl -u vector --since -2min`. There are no mounts: `/host/var/log` is a symlink to `/var/log`, and any other path is used as-is, but it must be readable by the `vector` user (`sudo -u vector head -1 <file>`; fix with `setfacl -m u:vector:r <file>`). Docker container logs are not collected natively. Native validate and test:

```sh
sudo sh -c 'set -a; . /etc/default/vector; [ ! -f /etc/vector/watch.d/local.vrl ] || export LEV_LOCAL_VRL=/etc/vector/watch.d/local.vrl; vector validate --no-environment --config-dir /etc/vector/watch.d /etc/vector/vector.yaml'
sudo env VECTOR_HOST=test-host VECTOR_SERVER_ID=test-server VECTOR_PROJECT_ID=test-project VECTOR_ENVIRONMENT=test \
  sh -c '[ ! -f /etc/vector/watch.d/local.vrl ] || export LEV_LOCAL_VRL=/etc/vector/watch.d/local.vrl; vector test /etc/vector/vector.yaml /etc/vector/watch.d/*.yaml'
```

`/var/log` is already mounted at `/host/var/log`. Docker's containers dir is mounted at `/host/docker/containers` on source servers. On the central host, add mounts to the `vector` service in the untracked `compose.override.yaml` (create it if missing); add the containers dir there when you first watch a container. Any other path P gets `- P:/host/P:ro`.

Validate and test (source server; on the central host `docker compose -f compose.tools.yaml run --rm vector-check` / `vector-test` do the same):

```sh
docker compose run --rm --no-deps --entrypoint sh vector -c '[ ! -f /etc/vector/watch.d/local.vrl ] || export LEV_LOCAL_VRL=/etc/vector/watch.d/local.vrl; vector validate --no-environment --config-dir /etc/vector/watch.d /etc/vector/vector.yaml'
# the shared tests assert these fixed labels, so tests always run with them:
docker compose run --rm --no-deps -e VECTOR_HOST=test-host -e VECTOR_SERVER_ID=test-server -e VECTOR_PROJECT_ID=test-project \
  -e VECTOR_ENVIRONMENT=test --entrypoint sh vector -c '[ ! -f /etc/vector/watch.d/local.vrl ] || export LEV_LOCAL_VRL=/etc/vector/watch.d/local.vrl; vector test /etc/vector/vector.yaml /etc/vector/watch.d/*.yaml'
```

## A. Install on a new server

Skip if a Lev Vector container is already running (`docker ps --format '{{.Image}}' | grep -E 'lev-vector|timberio/vector'`) or the native service is (`systemctl is-active vector`).

1. Check prerequisites: `docker compose version`. If Docker isn't installed, don't install it: ask the user whether to use the native install (step 5) instead. Persistent journal: `/var/log/journal` exists. If not, tell the user journald is volatile and only files/containers will be collected.
2. Copy `compose.yaml`, `.env.example` and an empty `watch.d/` from the Lev repo's `vector/` dir onto the server (e.g. `/opt/lev-vector`). Take them from the repo, not from memory. The shared `vector.yaml` ships inside the `ghcr.io/xvectorio/lev-vector` image.
3. Create `.env` from `.env.example` with `chmod 600`:
   - `VECTOR_HOST`: `hostname -s`.
   - `VECTOR_SERVER_ID` and `VECTOR_PROJECT_ID`: ask the user. These must be stable, low-cardinality IDs.
   - `JOURNAL_GID`: `getent group systemd-journal | cut -d: -f3`.
   - `VECTOR_ENVIRONMENT`: production, staging and so on.
   - `VECTOR_ENDPOINT`: the central base URL, without `/loki/api/v1/push`.
   - `VECTOR_PASSWORD`: ask the user to copy it from Lev → **Sources** → **Copy ingest password** and paste it into the file themselves. Don't handle it in chat.
   - Leave `VECTOR_CONTAINER_GLOB=SELECT_CONTAINER_ID`. Containers go in `watch.d`.
4. Validate, then `docker compose up -d`. Check logs for errors. `401` means the password is wrong; `connection refused`/TLS means the endpoint is wrong. The heartbeat appears on the Lev **Sources** page within about 2 minutes. Ask the user to confirm it if you can't see it.
5. **Native (no Docker, systemd only).** Download `vector-install.sh` from the Lev release the central host runs (`https://github.com/Xvectorio/lev/releases/download/v<version>/vector-install.sh`, or `releases/latest/download/…`) and run `sudo sh vector-install.sh`. Fill `/etc/default/vector` as in step 3, but without `JOURNAL_GID` (the `vector` user is already in `systemd-journal`); the user pastes the password there themselves. Validate (see **Native** above), then `systemctl enable --now vector` and check as in step 4. To upgrade, rerun a newer `vector-install.sh`.

## B. "Watch <thing>"

Find **every** log the thing writes, then add only what isn't already collected. Work through all four checks before writing anything.

1. **systemd / journald.** Run `systemctl list-units --all '*<thing>*'` and `systemctl --user list-units '*<thing>*'`, then `journalctl -u <unit> -p warning -n 20`. Journal logs are **already collected** by the shared `system_journal` source, so don't add a watch for them. The service label will be `_SYSTEMD_USER_UNIT`, `SYSLOG_IDENTIFIER` or `_SYSTEMD_UNIT`, in that order. Note which one applies.
2. **Docker.** Run `docker ps -a --filter name=<thing>` and check `docker inspect -f '{{.HostConfig.LogConfig.Type}}' <c>`:
   - `json-file`: add a file source `/host/docker/containers/<full id>/*-json.log` (the shared parser unwraps the envelope). Record the container name → id in the watch file comment. IDs change when a container is recreated, so tell the user to rerun "watch <thing>" after a redeploy.
   - `journald`: already collected, as in step 1.
   - Anything else (`local`, `syslog`, …): not supported. Tell the user.
3. **Files.**
   - Look for log paths in the thing's config: `grep -riE 'log(file|_file|path|_dir)?\s*[=:]|error_log|access_log' /etc/<thing>* <app dir>`.
   - Check `/var/log/<thing>*` and `/var/log/apps/<thing>/`. `/var/log/apps/*/*.log` is **already collected**, so skip it.
   - List open files: `ls -l /proc/$(pgrep -o <thing>)/fd 2>/dev/null | grep -i log`.
   - Also check where a container bind-mounts its logs (`docker inspect -f '{{json .Mounts}}'`).
4. **Choose.**
   - Collect error/application logs.
   - **Skip** access/audit/debug logs unless the user asks. Only warn+ lines are forwarded, and URLs containing "error" cause false positives.
   - **Skip** rotated or compressed files (`*.1`, `*.gz`). Vector follows rotation itself.
   - Skip anything not readable as root.
   - If a source is already collected, say so rather than adding a duplicate.

For each new source:

1. Read 20–50 real lines (`tail`). Find: the level format, whether multi-line stack traces occur, and any secrets or personal data the shared redaction misses. It covers bearer tokens and `password|secret|token|api_key|authorization|cookie` key=value.
2. Copy `template.yaml` from this skill to `watch.d/<thing>.yaml` and fill it in. Use real sample lines in the tests, with secrets replaced: at least one line that must reach Lev at the right level, and one routine line that must be dropped.
3. Add read-only mounts for new paths (native: none, just check the `vector` user can read them).
4. Validate and run the tests, and fix until both pass.
5. Apply: `up -d` (native: `systemctl restart vector`). A new mount recreates the container, but checkpoints persist in the `vector_data` volume.
6. Confirm in the Vector logs: `Found new file to watch` for each path, and no `ERROR`.

## C. Log format for your own apps

Use when writing or changing an app's logging so Lev groups, levels and triages it well, or when asked "how should my app log for lev". Every rule follows from `normalize` (level/fields), `multiline` (stack traces) and the incident fingerprint in `backend/core.py`.

**Preferred: one JSON object per line** on stdout/stderr (Docker `json-file`), the journal, or `/var/log/apps/<service>/*.log`. The last is collected automatically, with the service label taken from the directory name.

```json
{"timestamp":"2026-09-24T15:45:30.846+00:00","level":"error","service":"payments-api","message":"charge failed code=card_declined","order_id":"o-42"}
```

| Field | Rule |
|---|---|
| `level` | **Always set it.** `debug`, `info`, `warn`/`warning`, `error`, `fatal`/`critical`. Without it, level is guessed from words: an info line containing "failed" becomes an error, and an error line without those words is dropped. Only warn and above reach Lev. |
| `message` | A **constant sentence** plus stable identifiers. Also accepted as `msg`. See grouping below. |
| `timestamp` | RFC 3339 with an offset (`…+00:00` or `Z`). Also accepted as `time`. Otherwise the collection time is used. |
| `service` | Short and stable (`payments-api`). A watch file's `.lev_service` overrides it. |
| `request_id`, `order_id` | Appended to the message as `request_id=…`. The fingerprint ignores them, so they help investigation without splitting incidents. |
| **anything else** | **Dropped.** Output is limited to a fixed set of fields. Put context an operator needs into `message` (or into `request_id`/`order_id`). Never expect extra fields to become labels. |

**Grouping.** An incident is labels + message pattern. Before comparing, the fingerprint replaces only UUIDs, hex strings of 12+ characters, durations (`866ms`), `request_id`/`order_id`/`pid=` values and a leading timestamp. Any other varying value (counts, prices, order or req numbers, usernames, symbols, clock times) creates **a new incident and a new Jev call per value**.
- Good: `"charge failed code=card_declined"` with `order_id` as a field.
- Bad: `"charge 116057 for user bob failed at 4:13 PM"`.
- Put varying context in `order_id`/`request_id`, or leave it out and keep it in debug logs. If an existing app can't change its lines, add a service-scoped rule to `fingerprint()` (next to the `UFW BLOCK` rule) plus a test in `test_system.py`.
- Keep stable error codes in the text (`IB Error (202)`, `code=card_declined`). They separate real problems cheaply.

**Levels mean "an operator should look".**
- `warn`/`error` only for failed intended work, degraded service or exhausted resources.
- Connection churn, per-request tracing and expected retries belong at `info`/`debug`. Logging a WebSocket disconnect as 9 WARNING lines creates 9 incidents.
- Log **once per failure**, not once per retry iteration. Say how it ended: `"retry 3/5 failed, backing off"` at `warn`, then `"gave up after 5 retries"` at `error` or `"recovered after 2 retries"` at `info`. Jev routes a problem that explicitly recovered to *observe* and an unrecovered failure to *investigate*.

**Stack traces.**
- JSON: put the traceback inside `message` (joined with `\n`). One event keeps it together.
- Plain text: continuation lines must start with whitespace, `Traceback`, `Caused by:` or `SomethingError:`/`…Exception:`, and follow within 1.5 s. In `/var/log/apps` files, each new entry must start with a date or `{`.

**Never log secrets or personal data.** Redaction is a safety net, not a filter. It only catches `Bearer …` and `password|passwd|secret|token|api_key|authorization|cookie` followed by `=`/`:`.

**Plain-text apps you can't change.** Prefer `logfmt` (`level=error msg="…"`), which is parsed natively. Otherwise use a fixed level prefix mapped in a watch file (e.g. `ERROR:logger:msg` → `.lev_level`).

Python stdlib formatter that follows all of the above:

```python
import json, logging
from datetime import datetime, timezone

class LevFormatter(logging.Formatter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def format(self, r):
        msg = r.getMessage()
        if r.exc_info:
            msg += '\n' + self.formatException(r.exc_info)
        out = {'timestamp': datetime.fromtimestamp(r.created, timezone.utc).isoformat(),
               'level': r.levelname.lower(), 'service': self.service, 'message': msg}
        out.update(getattr(r, 'lev', {}))  # only request_id / order_id survive
        return json.dumps(out)

# log.error('charge failed code=card_declined', extra={'lev': {'order_id': oid}})
```

**Verify:**
1. Wrap a real output line in the Docker envelope (`{"log":"<line>\n","stream":"stdout","time":"…"}`).
2. Add a `vector test` case that inserts it at `normalize` and asserts `level`, `service` and the message on `relevant`.
3. Emit the same error with two different varying values and check the Log explorer or Incidents: it should be **one** incident.

## Report

End with a table: source (unit / container / path), status (**already collected** / **added** / **skipped: reason**), the `service` label it will appear under, and the level mapping. Then add:

- New data only shows up in Lev when the thing logs at warn or above (`read_from: end`, no backfill). Search `service=<label>` in the Log explorer to confirm.
- Anything the user must redo, e.g. refresh container ids after a redeploy.
