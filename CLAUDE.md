# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Ground rules

- **Everything runs in Docker.** Do not install Python/Node packages, Vector or a database on the host. Builds, tests, lockfile generation and Vector validation all go through Compose.
- Internal secrets (Postgres password, ingest password + hash, agent token) live in the `secrets` volume at `/run/lev`, generated once by the `init` service (`ops/init.sh`), which never overwrites them and seeds them from `.env` on upgrades. `.env` is optional (settings, `TYPESAFE_API_KEY`, and in dev `COMPOSE_FILE`). Never print or commit either.
- `compose.yaml` is the self-contained release file (images only, configs inline — `$$` escapes `$`). Builds and check services live in `compose.dev.yaml`; a dev `.env` sets `COMPOSE_FILE=compose.yaml:compose.dev.yaml[:compose.override.yaml]` so the commands below work unchanged.
- `README.md` is the operator-facing spec (stages, agent API bodies, reliability limits). Keep it in sync when behavior changes. The in-app `?` help (`ui/src/Help.svelte`, `TOPICS`) is a condensed copy of it: update both.

## Commands

```sh
docker compose up -d --build                     # stack at http://localhost:8080; first visit needs the setup code:
docker compose logs api | grep 'setup code'      # (only while no admin exists)
docker compose exec -it api python reset_admin.py   # set/reset an admin password
docker compose build                             # REQUIRED after backend/ui edits: code is baked into images
docker compose run --rm test                     # backend integration check (backend/test_system.py)
docker compose -f compose.tools.yaml run --rm vector-test   # Vector unit tests in vector/vector.yaml
docker compose -f compose.tools.yaml run --rm vector-check  # vector validate
docker compose run --rm eval-triage [--baseline]  # live Jev routing eval -> artifacts/triage-*.json (spends TypeSafe credits)
LEV_ADMIN_USER=… LEV_ADMIN_PASSWORD=… docker compose run --rm browser-check   # logs in; screenshots -> artifacts/
docker compose -f compose.tools.yaml run --rm --user "$(id -u):$(id -g)" ui-lock   # regenerate ui/package-lock.json
docker compose run --rm --entrypoint sh backup /restore-check.sh                  # backup restore check
docker compose logs --tail=50 worker             # also: api, vector, edge
```

- There is no pytest; `test_system.py` is a single assert-based `run()` that needs the live `postgres` and `loki` services. It isolates itself in a disposable `check_<uuid>` schema via `PGOPTIONS=search_path` and mocks all AI calls. To run a subset, edit/comment within `run()`—there is no per-test selector.
- UI type-check (`svelte-check`) and build run inside `ops/Dockerfile.edge`; a Svelte error fails `docker compose build edge`.
- Vector tests live as `tests:` blocks inside `vector/vector.yaml`; add regression cases there.

## Architecture

Data flow: **Vector → Caddy (`/loki/api/v1/push`, basic auth) → Loki (48h retention) → worker collector → PostgreSQL incidents → Jev triage → agent proposal → operator approval → agent verification → recurrence observation → resolved.**

- **Auth lives in `backend/app.py`'s `protect` middleware.** Operators: `lev_session` cookie → `sessions` table (scrypt hashes in `users`); the first admin is created via `/api/auth/setup` with the one-time code the API prints at startup while `users` is empty. `/api/agent/*` uses the bearer agent token. Operator POSTs (including `/api/auth/*`) require `X-Lev-Request: 1` (CSRF guard); the UI sends it. `request.state.user` is the audit actor. `ops/Caddyfile` serves the UI publicly, proxies `/api/*`, and basic-auths only the Loki push (`VECTOR_HASH` read from the secrets volume by `ops/edge.sh`). The UI (`/api/auth` state) shows setup/login before anything else.
- **`backend/core.py`**: shared DB connection (psycopg, env-configured `PG*`), `initialize()` applies idempotent `schema.sql` at startup (no migration tool — schema changes must be `IF NOT EXISTS`/idempotent), LogQL `selector()` (whitelisted labels: host, server_id, project_id, service, environment), Loki querying with recursive time-range splitting (`complete_logs`), `fingerprint()` which normalizes IDs/UUIDs/hex/durations and UFW packet fields for incident grouping, and `consolidate()` which re-keys incidents from older fingerprint rules at startup via `superseded_by`.
- **`backend/worker.py`**: one process, two loops. A daemon thread runs `collect` every 60s (nanosecond checkpoint + `LOOKBACK_SECONDS` overlap, dedup by event identity, one job per incident episode). The main loop runs `analyze_one` every 2s: Jev `/v1/systemone` Choice calls for category and actionability, then optional prose via `AI_*` OpenAI-compatible endpoint. Collection never waits on AI. The triage policy (model, instructions, categories, checks, gates) comes from `active_policy()`: immutable `policies` versions in PostgreSQL, edited in the UI; `DEFAULT_POLICY` (plus the `TRIAGE_CONFIDENCE`/`OBSERVE_CONFIDENCE`/`TYPESAFE_MODEL` env) only seeds version 1. Operator verdicts live in `labels` (with an evidence snapshot); `/api/jev/insight` re-routes stored answers under candidate gates without provider calls. `replay_one` (after `analyze_one`, same advisory lock, only when no job is due) judges labelled evidence with a chosen version into `replays`; `/api/jev/suggest` asks the `AI_*` model (via `chat_json`) for criteria edits. Provider keys come from `core.setting()` (Settings page, falling back to `.env`).
- **`backend/app.py`**: FastAPI (docs disabled). Operator endpoints under `/api/` (search, incidents, analyze/"Triage again", approve, status); agent endpoints under `/api/agent/tasks` (fetch, proposal, verify). Proposals/approvals are bound to incident `generation`; recurrence bumps the generation and invalidates prior approvals. Every transition writes to the `audit` table.
- **`ui/src/App.svelte`**: the whole Svelte 5 UI in one component (Incidents workspace + log explorer). Served as static files by Caddy; strict CSP (`'self'` only) — no inline scripts/styles or external assets.
- **`vector/vector.yaml`**: baked into the `lev-vector` image (`vector/Dockerfile`), used by the central stack's local `vector` service and remote source servers (`vector/compose.yaml`); the dev overlay bind-mounts the repo copy. Does severity normalization, multiline joining, secret redaction and field whitelisting *before* the disk buffer; only warn/error/fatal plus a heartbeat reach Loki. Resolution depends on that heartbeat. Per-server sources are gitignored drop-ins in `vector/watch.d/*.yaml` (host mounts in gitignored `compose.override.yaml`) (`src_<x>` + `watch_<x>` remap, matched by the `"watch_*"` input; `watch_none` keeps the wildcard valid when empty); `vector-test` also runs their `tests:`.

## Design constraints to preserve

- No autonomous executor: the backend never runs commands or treats log text as instructions. Agents act externally within their own scope.
- No Redis/Celery; PostgreSQL uniqueness constraints provide job/result dedup.
- Loki labels must stay low-cardinality; request/order IDs stay in the log body.
- Use the TypeSafe skill (`typesafe:typesafe-ai`) when changing Jev triage.
- Project skills in `.claude/skills/`: `lev-agent` (agent task API via `lw.sh`; never use admin credentials or the approve endpoint) and `lev-vector` (Vector install + `watch.d` sources; never edit the shared pipeline per server). Keep them in sync with the agent API in `app.py` and the `normalize` contract in `vector.yaml`.
