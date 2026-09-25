CREATE TABLE IF NOT EXISTS incidents (
  id text PRIMARY KEY,
  labels jsonb NOT NULL,
  pattern text NOT NULL,
  occurrences bigint NOT NULL DEFAULT 0,
  first_ns bigint NOT NULL,
  last_ns bigint NOT NULL,
  summary text,
  suspected_cause text,
  suggested_checks jsonb NOT NULL DEFAULT '[]',
  analyzed_at timestamptz
);
CREATE TABLE IF NOT EXISTS events (
  id text PRIMARY KEY,
  incident_id text NOT NULL REFERENCES incidents(id),
  ts_ns bigint NOT NULL,
  labels jsonb NOT NULL,
  message text NOT NULL
);
CREATE INDEX IF NOT EXISTS events_incident_time ON events(incident_id, ts_ns DESC);
CREATE INDEX IF NOT EXISTS events_time ON events(ts_ns);
CREATE TABLE IF NOT EXISTS jobs (
  id text PRIMARY KEY,
  incident_id text NOT NULL REFERENCES incidents(id),
  status text NOT NULL DEFAULT 'pending',
  attempts integer NOT NULL DEFAULT 0,
  next_attempt timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  error text,
  result jsonb,
  evidence jsonb
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(next_attempt) WHERE status != 'done';
CREATE TABLE IF NOT EXISTS worker_state (
  name text PRIMARY KEY,
  checkpoint_ns bigint NOT NULL DEFAULT 0,
  heartbeat timestamptz NOT NULL DEFAULT now(),
  error text
);
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'new';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'unknown';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS triage jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS generation integer NOT NULL DEFAULT 1;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS proposal jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS verification jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS verification_ns bigint;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS resolved_ns bigint;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS saved_evidence jsonb NOT NULL DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS generation integer NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  incident_id text NOT NULL REFERENCES incidents(id),
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_incident ON audit(incident_id,id DESC);

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS superseded_by text;
-- Worst level seen: warn < error < fatal. NULL until an event arrives after this column existed.
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS level text;
CREATE TABLE IF NOT EXISTS settings (
  name text PRIMARY KEY,
  value jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  username text PRIMARY KEY,
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash text PRIMARY KEY,
  username text NOT NULL REFERENCES users ON DELETE CASCADE ON UPDATE CASCADE,
  expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  author text NOT NULL,
  note text NOT NULL DEFAULT '',
  config jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
  incident_id text PRIMARY KEY REFERENCES incidents(id),
  route text NOT NULL,
  category text,
  actor text NOT NULL,
  at timestamptz NOT NULL DEFAULT now(),
  evidence jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS replays (
  policy_id integer NOT NULL REFERENCES policies(id),
  incident_id text NOT NULL REFERENCES incidents(id),
  attempt integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  result jsonb,
  error text,
  PRIMARY KEY (policy_id, incident_id)
);
