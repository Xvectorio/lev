# Lev — a Log Event Verifier that uses Jev

Lev turns the warnings and errors from your servers and apps into a short queue of incidents, then tracks each one until a fix is **verified**: not just applied, but shown to hold.

Triage is powered by **Jev**, the System One model from [TypeSafe](https://typesafe.ai/). It makes typed, confidence-scored judgments about what each incident is and whether it needs action.

Logs are short-lived evidence. The durable record is the incident, the proposed fix, who approved it and whether the problem came back.

## How it works

```
Vector ──▶ Loki ──▶ incidents ──▶ Jev triage ──▶ agent proposal ──▶ operator approval ──▶ agent applies & checks ──▶ no recurrence ──▶ resolved
(per server)  (48h)   (Postgres)    (TypeSafe)                            (you, in the UI)                                  (15 min + heartbeat)
```

1. **Collect.** [Vector](https://vector.dev) on each server reads the journal, Docker and log files, normalizes severity, joins multi-line stack traces and redacts secrets. Only warn/error/fatal lines plus a heartbeat are shipped to Loki.
2. **Group.** A worker fingerprints each event (IDs, UUIDs, hex values and durations are normalized) so repeats of the same problem become one incident with a count, not a thousand log lines.
3. **Triage.** [Jev](https://typesafe.ai) (TypeSafe) makes typed judgments on category and actionability, with a confidence gate. Uncertain results go to human review instead of guessing.
4. **Propose.** An external agent (for example Claude Code with the bundled `lev-agent` skill) investigates read-only and submits a diagnosis, the exact changes, acceptance checks and a rollback plan.
5. **Approve.** An operator approves that specific proposal in the UI. Lev itself never runs commands or treats log text as instructions.
6. **Verify.** The agent applies the fix and reports its checks. Lev resolves the incident only after 15 minutes with no recurrence and a live heartbeat from the source. If the problem comes back, the incident reopens and old approvals are invalidated.

## Features

- Incident queue and a log explorer with search, filters and a histogram
- Per-server log sources as drop-in files (`vector/watch.d/`), with Vector unit tests
- Full audit trail for every state change
- One-command install: secrets generated on first start, admin account created in the browser
- Session-based operator login, bearer-token agent API, CSRF guard and a strict CSP
- Daily PostgreSQL backups with a restore check
- Claude Code skills: `lev-agent` (works the incident queue) and `lev-vector` (installs Vector and adds log sources)

**Stack:** Svelte 5 · FastAPI · PostgreSQL · Loki · Vector · Caddy. No Redis or Celery.

## Tech stack

| Layer | Technology |
|---|---|
| Log shipping | [Vector](https://vector.dev) on each server (severity normalization, multiline joining, secret redaction, disk buffer) |
| Log storage | [Grafana Loki](https://grafana.com/oss/loki/) 3.6, 48h retention |
| Database | PostgreSQL 17 (incidents, jobs, evidence, audit trail; no Redis/Celery) |
| Backend | Python 3.13, FastAPI, Uvicorn, psycopg 3, httpx |
| AI triage | [Jev](https://typesafe.ai/) (TypeSafe System One) typed Choice judgments; optional OpenAI-compatible model for prose |
| UI | Svelte 5, TypeScript, Vite |
| Edge | Caddy 2.10 (static UI, reverse proxy, automatic HTTPS, ingest basic auth) |
| Runtime | Docker Compose; tests with Playwright/Chromium in containers |

## Quick start

The host needs Docker Engine with Compose v2.23 or newer. No git checkout is required:

```sh
mkdir lev && cd lev
curl -fsSLO https://github.com/jelcke/lev/releases/latest/download/compose.yaml
docker compose up -d
docker compose logs api | grep 'setup code'
```

Open http://<server>:8080 (http://localhost:8080 on the same machine) and enter the setup code, then choose your admin username and password. On first start Lev generates its internal secrets (database password, ingest password, agent token) into a Docker volume. Every data volume starts empty.

To turn on triage, create a `.env` next to `compose.yaml` with `TYPESAFE_API_KEY=...` and run `docker compose up -d` again. [`.env.example`](.env.example) lists every optional setting.

---

The rest of this document is the operator guide.

**Everything runs in Docker**, including builds, tests and Vector on source servers. The host needs Docker Engine and Compose only. No host Python packages, Node packages, database, web server or Vector installation is needed.

## Install, upgrade and accounts

`compose.yaml` is self-contained: config files are baked into the `lev-api`, `lev-edge` and `lev-vector` images or inlined in the file, and a one-shot `init` service writes the generated secrets to the `secrets` volume without ever overwriting them. Each release attaches a `compose.yaml` pinned to its version (`LEV_VERSION` in `.env` overrides it). To upgrade, download the newer release's `compose.yaml` over the old one, then run `docker compose pull && docker compose up -d`; volumes and secrets are kept.

The first visit shows **Create admin account**. It asks for a one-time setup code that only appears in `docker compose logs api`, so whoever reaches the page first cannot claim the instance. Passwords need at least 12 characters and are stored as scrypt hashes. Sessions last 7 days in an HttpOnly, SameSite=Strict cookie. To reset a password or add another admin:

```sh
docker compose exec -it api python reset_admin.py
```

The ingest password and agent token are on the **Sources** page (**Copy ingest password**, **Copy agent token**).

Upgrading from a pre-release install that used `ops/setup.sh`: keep your `.env`. The `init` service copies `POSTGRES_PASSWORD`, `VECTOR_PASSWORD` and `AGENT_TOKEN` from it, so the database, source servers and agents keep working. The old `ADMIN_*` values are no longer used; create the admin with the setup code.

The **Theme** control offers System, Light and Dark modes. Your choice is saved in your browser; System follows your device’s color preference.

## Stack and workflow

- **Svelte 5 + TypeScript + Vite**, static assets served by Caddy. The **Incidents** workspace opens first; a Splunk-inspired explorer offers search, time/service/host/severity filters, fields and an interactive histogram of the loaded events. The histogram is explicitly a bounded result view, not a full-volume metric.
- **FastAPI + PostgreSQL** serve the app and persist checkpoints, deduplication keys, jobs, retained evidence and the audit trail. No Redis or Celery.
- **Vector → Loki**, independently of PostgreSQL and AI. Loki stores raw evidence for 48 hours; selected issue examples/context survive in PostgreSQL. Loki's compactor deletes asynchronously, so physical deletion is not exactly at hour 48.
- **Jev** classifies category and actionability using typed Choice questions at `/v1/systemone`. Model/version, probabilities and confidence are retained. The default `0.8` confidence gate is a starting setting, not a validated accuracy guarantee; tune it on representative labelled logs.
- An optional **text model** produces explanations, separate from Jev's structured judgments.

Incident stages:

| Stage | Meaning and next action |
|---|---|
| `new` | Evidence was grouped into an incident; AI triage is pending. |
| `ready` | Jev found an actionable incident with sufficient confidence; an external agent can investigate. |
| `review` | Classification is uncertain, or a fix failed its checks; review evidence before proceeding. |
| `observing` | Classified as transient or benign, or dismissed by an operator (reason optional) or an agent (reason required); new occurrences update the count. Use **Triage again** when investigation is warranted. |
| `proposed` | An agent submitted its diagnosis, exact changes, acceptance checks and rollback plan; an operator reviews them. |
| `approved` | An operator approved that specific proposal for this episode; the external agent can apply it within its authorized scope. |
| `verifying` | The agent reported every approved check as passed; Lev observes for recurrence. |
| `resolved` | The collector caught up past 15 minutes of observation, found no recurrence, and confirmed a recent source heartbeat. |


```
new → ready / review / observing → proposed → approved → verifying → resolved
                                                      ↘ recurrence → new episode
```

A low-confidence or unknown result goes to review. Benign/transient classifications remain observable. An agent exports/fetches the task, investigates within its own authorized scope, submits a concrete proposal and waits for operator approval. Reported successful acceptance checks enter a 15-minute observation period. Resolution requires a successful collector pass and a recent source heartbeat; recurrence reopens the incident and invalidates prior approvals.

The application does **not contain an autonomous SSH executor**. Agent tasks and a complete proposal/approval/verification API are implemented; connect an external agent with separately scoped host/repository access. The backend never interprets log contents as shell commands. Until a specific agent and target hosts are configured, no machine is modified.

## This server's logs

The central Compose stack now includes Vector for this host. It reads the persistent system journal through a read-only `/var/log` mount and watches `/var/log/apps/<service>/*.log` if those files exist. It forwards warnings/errors/fatal events and a collector heartbeat through Caddy to Loki, with a persistent disk buffer and journal checkpoints. Collection starts with new events rather than importing the whole journal.

This host's identity defaults to **server `local`**, **project `host-infrastructure`**, **environment `development`**. In **Log explorer**, set **Server ID** to `local` to see these events. Matching incidents appear automatically after the worker's next collection pass.

Change `LOCAL_SERVER_ID`, `LOCAL_PROJECT_ID`, `LOCAL_ENVIRONMENT` or `LOCAL_JOURNAL_GID` (the group owning `/var/log/journal`: `stat -c %g /var/log/journal`) in `.env`, then run `docker compose up -d vector`. Vector runs as root in its container, so the journal group only matters on hosts that restrict root.

Host-specific log sources go in `vector/watch.d/<name>.yaml` next to `compose.yaml` (see `.claude/skills/lev-vector/template.yaml`) and their read-only mounts in `compose.override.yaml`, which Compose loads automatically. In a git checkout both are gitignored, so your local sources never end up in the repo.

The local collector reads Docker daemon messages from the journal, but does not ingest every container's stdout/stderr. For selected container output, use the source-server configuration below. The local collector does not mount the Docker socket.

Useful commands:

```sh
docker compose logs --tail=50 vector
docker compose restart vector
```

## Add a source server (Docker only)

On the source server, download `vector-compose.yaml` and `vector.env.example` from the [latest release](https://github.com/jelcke/lev/releases/latest) into a directory such as `/opt/lev-vector`, rename them to `compose.yaml` and `.env` (`chmod 600 .env`), create an empty `watch.d/` directory, and set:

- `VECTOR_PROJECT_ID`: stable, human-readable project ID (for example `payments-prod`).
- `VECTOR_SERVER_ID`: stable server ID (for example `eu-west-1-app-03`). This is separate from `VECTOR_HOST`, which is the machine hostname shown for context.
- `VECTOR_HOST`: machine hostname.
- `JOURNAL_GID`: numeric group ID for host `systemd-journal` (`getent group systemd-journal`).
- `VECTOR_ENVIRONMENT`: production/staging/etc.
- `VECTOR_ENDPOINT`: central HTTPS URL, without `/loki/api/v1/push`.
- `VECTOR_PASSWORD`: from Lev → **Sources** → **Copy ingest password**.

The shared pipeline (`vector/vector.yaml` in this repo) ships inside the `ghcr.io/jelcke/lev-vector` image.

Per-server log sources go in `vector/watch.d/<thing>.yaml` (a `src_<thing>` source plus a `watch_<thing>` remap that sets `.lev_service`, optionally `.lev_level`); the shared `vector.yaml` picks up every `watch_*` transform and stays identical on all servers. Each watch file carries its own `tests:`; `vector-test` runs them with the shared tests. Claude Code can install Vector and add these with the `.claude/skills/lev-vector` skill ("watch nginx"): it finds a service's journald, container and file logs, adds only what is not already collected, and tests parsing with real sample lines. To use it on a source server, copy the skill folder to `~/.claude/skills/`.

Review the source paths and service selection in [`vector/vector.yaml`](vector/vector.yaml), then start it:

```sh
docker compose up -d
```

The supplied config tails `/var/log/apps/<service>/*.log`, persistent journald, and only Docker `json-file` containers selected by `VECTOR_CONTAINER_GLOB`. Change the example `SELECT_CONTAINER_ID` in the source server's `.env` to IDs of relevant containers before starting Vector. If Docker uses `journald`, read those events from journald instead. Other Docker logging drivers require their corresponding source; do not assume `local` driver files are JSON. Prefer structured app logs with a stable `service`, `level`, `timestamp`, `message`, `request_id` and `order_id`.

To collect specific containers (including this Lev stack's own, via `docker compose ps -q api worker edge postgres loki`), add a `vector/watch.d/<name>.yaml` with a file source per container, `/host/docker/containers/<id>/*-json.log`, instead of editing `vector.yaml`; on the central host also mount `/var/lib/docker/containers:/host/docker/containers:ro` on the `vector` service. Container IDs change when containers are recreated, so refresh the watch file after redeploys. `VECTOR_CONTAINER_GLOB` remains as a single-container shortcut; it is not auto-discovery.

The source has read-only host log mounts and a persistent 512 MiB disk buffer. It does not mount the Docker socket or run privileged. Every event gets `project_id`, `server_id`, `host`, `environment` and `service`; the project and server IDs are Loki labels, searchable in the Log explorer, and displayed on incident cards and log details. Use stable IDs and keep them low-cardinality. Journald defaults to `/var/log/journal`; for volatile journals, change its directory argument and mount `/run/log/journal` read-only. Restrict `include_units`/`include_matches` for your infrastructure to avoid collecting unrelated services. Do not collect the same app through both its file and journal source.

Only warnings, errors and fatal events (plus one small collector heartbeat per minute) are sent to central storage. ISO timestamps are parsed, common exception continuations are joined, and common key/value credentials and bearer tokens are redacted **before the disk sink buffer**. Only whitelisted fields are forwarded. Redaction is pattern-based: test service-specific secret formats before onboarding a source. IDs stay in the body, not Loki labels. Container logs without a structured service name fall back to `container`; configure the application service field for useful targeting.

Sources start at the current end on first installation. Checkpoints resume on restart. A full buffer applies backpressure, but logs can still expire from the source's own rotation during long outages. Keep source rotation longer than your expected outage budget. Loki rejects samples older than 48 hours, and late samples may also fall outside its per-stream ordering window.

## Agent integration

Claude Code can act as this agent with the project skill `.claude/skills/lev-agent` (`/lev-agent`, or `/loop 30m /lev-agent`). It investigates read-only, proposes, stops at operator approval, then applies and verifies only the approved proposal. Copy the folder to `~/.claude/skills/` and set `LEV_URL`/`LEV_AGENT_TOKEN` to use it on a source server. It then picks up only that server's incidents, using `VECTOR_SERVER_ID` from the Vector `.env` (`LEV_VECTOR_ENV`, default `/opt/lev-vector/.env`; `LEV_SERVER_ID` overrides).

Use `Authorization: Bearer <agent token>` (Lev → **Sources** → **Copy agent token**). This separate token grants task read/proposal/verification access, **not operator approval**. Treat it as a trusted integration credential. Use one agent consumer per `server_id`; there is no multi-agent execution lease. The `server_id` filter is routing only: the shared token can still act on any incident.

| Request | Purpose |
|---|---|
| `GET /api/agent/tasks[?server_id=X]` | Ready and approved tasks, optionally only those from one source server |
| `GET /api/agent/tasks/{id}` | Target, episode, evidence, diagnostic checks, acceptance and exact approved proposal |
| `POST /api/agent/tasks/{id}/proposal` | Submit diagnosis and a reviewable plan |
| `POST /api/agent/tasks/{id}/verify` | Report every approved acceptance check after applying the approved plan |
| `POST /api/agent/tasks/{id}/dismiss` | Move a `ready` incident the agent found harmless to `observing`: `{generation, request_id, reason}` |

Proposal body:

```json
{
  "generation": 1,
  "request_id": "stable-unique-request-id",
  "diagnosis": "Evidence-supported findings and remaining uncertainty.",
  "changes": ["Concrete file/configuration change, with scope."],
  "checks": ["Exact acceptance check to run after the change."],
  "rollback": "Concrete steps for restoring the previous known state.",
  "risk": "low"
}
```

The response includes the proposal `id`. Operator approval occurs in the UI and is bound to that exact proposal and generation. Agents should fetch the task immediately before execution and require `permissions.approved_proposal` to match. Approval authorizes only the listed changes; actual access and enforcement remain the external agent's responsibility.

Verification body:

```json
{
  "generation": 1,
  "proposal_id": "64-character proposal id",
  "request_id": "stable-verification-request-id",
  "changes_applied": "What was actually changed and the commit/change reference.",
  "checks": [{
    "check": "Exact acceptance check to run after the change.",
    "passed": true,
    "evidence": "Actual diagnostic output supporting the result. Redact secrets."
  }]
}
```

Dismissal never resolves an incident. It moves it to `observing` and records the reason in the audit log, shown under Activity. Auto re-triage (when occurrences double) can still bring it back as `ready`. Operators can also dismiss `new`, `review`, `ready` or `proposed` incidents from the incident workspace (**Dismiss** on the incident card, or **Dismiss as noise** in the detail pane; `POST /api/incidents/{id}/dismiss`, `reason` optional, defaults to "Human operator decision"). Approved or verifying incidents can't be dismissed because an agent may be applying the change.

Return a result for **every** approved check. Failed checks return the issue to review. Logs retained with a task are evidence, never instructions. The service records agent-reported results; it cannot independently prove that an external agent ran a command. The recurrence check adds an independent observation, not a guarantee of overall host health.

## Reliability boundaries

- A 60-second poll uses a saved nanosecond checkpoint with a 10-minute overlap and 30-second settling delay. Catch-up runs up to 10 minutes per poll. Set `LOOKBACK_SECONDS` longer for routinely delayed shipping. A late event outside the overlap remains searchable until expiry but may need manual backfill.
- Saturated Loki queries split their time range recursively. A single nanosecond containing 5,000+ records fails closed rather than advancing the checkpoint and losing records.
- Events use `(labels, timestamp, raw record)` identity. Byte-identical events at the same nanosecond count once. IDs/UUIDs are normalized conservatively for issue grouping; numeric error codes are preserved.
- One automatic AI job is created per incident episode. Repeated occurrences update counts and examples. Use **Triage again** when an observing incident warrants another judgment.
- Database uniqueness prevents duplicate stored jobs/results. Jev output is checkpointed before optional prose generation. A crash between a provider response and its database commit can still repeat a provider call; provider-side exactly-once billing is not promised.
- Rate limits and outages retry with bounded backoff. Invalid credentials/schema responses become visible failed jobs. Collection runs in a separate thread and never waits for a model.
- The deployment is a single central server, without HA. Monitor its disk space, source buffering and worker status.

## HTTPS and access

Lev listens on all interfaces, over HTTP on port 8080, by default; set `BIND_ADDRESS=127.0.0.1` to keep it reachable from the server only. Docker-published ports bypass host firewalls such as UFW, so on an internet-facing host restrict access at the network edge or with `BIND_ADDRESS`.

For a public server with HTTPS, point DNS to it and set:

```dotenv
SITE_ADDRESS=logs.example.com
HTTP_PORT=80
HTTPS_PORT=443
```

Then `docker compose up -d`. Caddy obtains/renews HTTPS certificates when DNS and ports 80/443 are reachable. PostgreSQL, Loki and FastAPI publish no host ports. Operator accounts are managed with `reset_admin.py` (see above); there is no self-service signup. Session cookies are marked Secure when served over HTTPS. Failed logins are delayed by one second, but there is no lockout, so use a long password on an internet-facing server.

## Backups and restore

The backup container creates a PostgreSQL custom-format dump daily in `backups/`, retaining 14 days. It includes incident evidence, proposals, audit, checkpoint and jobs. Copy these dumps **off-server** using your existing encrypted backup destination; local copies alone do not protect against host loss. Save the `secrets` volume (`docker run --rm -v lev_secrets:/s alpine tar c -C /s .`) and any `.env` securely with your recovery material; a restored database needs its original `postgres_password`. Raw Loki logs deliberately have no long-term archive.

Restore into a fresh database first. The executable restore check creates a disposable database, restores a fresh dump and checks the tables without changing the live database:

```sh
docker compose run --rm --entrypoint sh backup /restore-check.sh
```

For disaster recovery, start PostgreSQL on the replacement server, restore your chosen dump with `pg_restore --no-owner --exit-on-error` into the empty `lev` database, then start the other services. Reconnect source containers; their buffers replay what remains. If a checkpoint predates raw retention, status reports the resulting gap.

## Verification and development

Development builds from source with the `compose.dev.yaml` overlay. Enable it once in `.env`:

```dotenv
COMPOSE_FILE=compose.yaml:compose.dev.yaml
```

(Append `:compose.override.yaml` if you use one; `COMPOSE_FILE` replaces the automatic override loading.) Then:

```sh
# All tools and dependencies stay inside containers.
docker compose up -d --build
docker compose -f compose.tools.yaml run --rm vector-test
docker compose run --rm test
LEV_ADMIN_USER=admin LEV_ADMIN_PASSWORD='…' docker compose run --rm browser-check
```

The backend integration check uses real PostgreSQL and Loki, a disposable DB schema, and mocked AI responses. It checks first-run setup, login/logout, provider outage, deduplication, checkpoint progress, Jev request/response shape, stale running-job recovery, authentication, approvals, complete acceptance results, recurrence and evidence retention. Vector tests cover redaction, timestamp parsing, Docker envelopes and multiline exceptions. Browser checks log in with an existing account, run Chromium in Docker and save screenshots to `artifacts/`.

If UI dependencies change, regenerate only the lockfile through Docker:

```sh
docker compose -f compose.tools.yaml run --rm --user "$(id -u):$(id -g)" ui-lock
```

Use the installed [TypeSafe skill](https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md) for changes to triage. Canonical contracts: [Choice](https://docs.typesafe.ai/primitives/choice), [HTTP API](https://docs.typesafe.ai/api), [confidence](https://docs.typesafe.ai/confidence). Collection references: [Vector Loki sink](https://vector.dev/docs/reference/configuration/sinks/loki/) and [Loki retention](https://grafana.com/docs/loki/latest/operations/storage/retention/).

## License

Copyright (C) 2026 Xvector.

Lev is licensed under the [GNU Affero General Public License v3.0](LICENSE). If you run a modified version as a network service, you must offer its source to its users. Commercial licenses without this obligation are available from Xvector.

Contributions require signing the [Contributor License Agreement](CLA.md); see [CONTRIBUTING.md](CONTRIBUTING.md).
