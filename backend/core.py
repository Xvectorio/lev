from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

NS = 1_000_000_000
# Loki keeps raw logs this long (compose passes the same value to Loki's limits_config); minimum 24.
RETENTION_H = max(24, int(os.getenv('LOG_RETENTION_HOURS', '48')))
RETENTION = RETENTION_H * 3600 * NS
LOKI = os.getenv('LOKI_URL', 'http://loki:3100')


def secret(name):
    # The environment wins (tests, upgrades from a .env); otherwise the file the init service generated.
    try:
        return os.getenv(name.upper()) or Path('/run/lev', name).read_text().strip()
    except FileNotFoundError:
        return ''


# Runtime settings editable on the Settings page; a saved value overrides the .env one.
# name: secret. Provider URLs are env-only: a URL editable here would let any operator send the keys to their own server.
SETTINGS = {'TYPESAFE_API_KEY': True, 'AI_API_KEY': True, 'AI_MODEL': False}


def setting(name, conn=None):
    if conn is None:
        with db() as conn:
            return setting(name, conn)
    row = conn.execute("SELECT value->>%s AS v FROM settings WHERE name='config'", (name,)).fetchone()
    return (row and row['v']) or os.getenv(name, '')


os.environ['PGPASSWORD'] = os.getenv('PGPASSWORD') or secret('postgres_password')
# Internal SQL fragment, never constructed from user input; queries use alias i.
SAMPLE_SQL = "(coalesce(i.labels->>'environment','')='demo' OR coalesce(i.labels->>'project_id','')='lev-test' OR coalesce(i.labels->>'host','') ~ '^check_[0-9a-f]{32}$')"



def db():
    return psycopg.connect('', row_factory=dict_row, connect_timeout=5)


def initialize():
    with db() as conn:
        conn.execute(Path(__file__).with_name('schema.sql').read_text())
        consolidate(conn)


def consolidate(conn):
    # Re-key incidents grouped by older fingerprint rules. Originals stay for audit; events keep their incident.
    conn.execute('SELECT pg_advisory_xact_lock(41003)')
    for item in conn.execute(f"""SELECT * FROM incidents i WHERE superseded_by IS NULL AND NOT {SAMPLE_SQL}
            AND status NOT IN ('proposed','approved','verifying')""").fetchall():
        target, pattern = fingerprint({'labels': item['labels'], 'message': item['pattern']})
        if target == item['id']:
            continue
        conn.execute("""INSERT INTO incidents(id,labels,pattern,first_ns,last_ns,saved_evidence)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (target, Jsonb(item['labels']), pattern[:8000],
            item['first_ns'], item['last_ns'], Jsonb(item['saved_evidence'])))
        conn.execute("""UPDATE incidents SET occurrences=occurrences+%s,first_ns=least(first_ns,%s),
            last_ns=greatest(last_ns,%s) WHERE id=%s""", (item['occurrences'], item['first_ns'], item['last_ns'], target))
        conn.execute('UPDATE incidents SET superseded_by=%s WHERE id=%s', (target, item['id']))
        conn.execute("INSERT INTO audit(incident_id,actor,action,data) VALUES (%s,'system','consolidated',%s)",
                     (item['id'], Jsonb({'into': target})))
        conn.execute("""INSERT INTO jobs(id,incident_id,generation,evidence) SELECT %s,id,generation,
            jsonb_build_object('examples',saved_evidence) FROM incidents WHERE id=%s ON CONFLICT DO NOTHING""",
                     (digest([target, 1]), target))
    # Re-triage review items judged before routing was versioned (triage-v2); once per incident.
    conn.execute("""INSERT INTO jobs(id,incident_id,generation,evidence)
        SELECT encode(sha256(('unversioned:'||id)::bytea),'hex'),id,generation,jsonb_build_object('examples',saved_evidence)
        FROM incidents WHERE superseded_by IS NULL AND status='review' AND triage IS NOT NULL
        AND NOT triage ? 'policy_version' ON CONFLICT DO NOTHING""")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def selector(labels=None, severity='', text=''):
    parts = [f'{key}={json.dumps(value)}' for key, value in (labels or {}).items()
             if value and key in ('host', 'server_id', 'project_id', 'service', 'environment')]
    query = '{' + ','.join(parts or ['service=~".+"']) + '}'
    if severity:
        query += f' | json | level={json.dumps(severity)}'
    # Splunk-style terms: every word or "quoted phrase" must match; NOT excludes the next term.
    negate = False
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', text):
        if word == 'NOT':
            negate = True
        elif phrase or word:
            query += (' != ' if negate else ' |= ') + json.dumps(phrase or word)
            negate = False
    return query


def logs(query, start, end, limit=500, direction='backward'):
    response = httpx.get(LOKI + '/loki/api/v1/query_range', params={
        'query': query, 'start': str(start), 'end': str(end),
        'limit': limit, 'direction': direction}, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get('status') != 'success':
        raise RuntimeError('Loki query failed')
    rows = []
    for stream in result['data']['result']:
        for ts, line, *_ in stream['values']:
            try:
                data = json.loads(line)
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            labels = {key: stream['stream'].get(key, 'unknown')
                      for key in ('host', 'server_id', 'project_id', 'service', 'environment')}
            rows.append({'ts_ns': str(ts), 'labels': labels,
                         'message': str(data.get('message', line)),
                         'level': str(data.get('level', 'info')), 'raw': line})
    return sorted(rows, key=lambda row: int(row['ts_ns']), reverse=direction == 'backward')


def complete_logs(query, start, end):
    # Loki ranges are [start, end): the halves share `middle` as one's end and the other's start.
    rows = logs(query, start, end, 5000, 'forward')
    if len(rows) < 5000:
        return rows
    if end - start <= 1:
        raise RuntimeError('Over 4999 logs at one nanosecond; checkpoint held, split by service')
    middle = (start + end) // 2
    return complete_logs(query, start, middle) + complete_logs(query, middle, end)


def fingerprint(row):
    # ponytail: heuristic grouping; use service-specific templates if distinct errors merge.
    if row['message'].startswith('[UFW BLOCK]'):
        # Keep interface/destination/protocol/service port; drop rotating sources and ephemeral ports.
        fields = dict(re.findall(r'\b(IN|OUT|DST|PROTO|DPT|TYPE|CODE)=([^\s]*)', row['message']))
        if 'DST' in fields and 'PROTO' in fields:
            if fields.get('DPT', '').isdigit() and int(fields['DPT']) >= 32768:
                fields['DPT'] = '<ephemeral>'
            pattern = '[UFW BLOCK] ' + ' '.join(f'{key}={fields[key]}' for key in ('IN','OUT','DST','PROTO','DPT','TYPE','CODE') if key in fields)
            return digest([row['labels'], pattern]), pattern
    pattern = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b', '<id>', row['message'])
    pattern = re.sub(r'\b(?:request_id|order_id|pid)=[\w-]+', '<id>', pattern)
    pattern = re.sub(r'\b[0-9a-f]{12,}\b', '<hex>', pattern)
    pattern = re.sub(r'\b\d+(?:\.\d+)?(?:ns|µs|us|ms|s)\b', '<dur>', pattern)
    pattern = re.sub(r'^(?:time="[^"]*"|\d{4}-\d\d-\d\d[T ][\d:.+Z-]+)\s*', '', pattern)
    return digest([row['labels'], pattern]), pattern


def ingest(conn, rows):
    grouped = {}
    for row in rows:
        incident_id, pattern = fingerprint(row)
        event_id = digest([row['labels'], row['ts_ns'], row['raw']])
        ts = int(row['ts_ns'])
        conn.execute('''INSERT INTO incidents(id, labels, pattern, first_ns, last_ns)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                     (incident_id, Jsonb(row['labels']), pattern[:8000], ts, ts))
        inserted = conn.execute('''INSERT INTO events VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id''',
            (event_id, incident_id, ts, Jsonb(row['labels']), row['message'][:16000])).fetchone()
        if inserted:
            item = conn.execute('SELECT * FROM incidents WHERE id=%s FOR UPDATE', (incident_id,)).fetchone()
            if item['status'] in ('resolved', 'verifying') and ts > (item['verification_ns'] or 0):
                conn.execute('''UPDATE incidents SET status='new',generation=generation+1,
                    proposal=NULL,verification=NULL,verification_ns=NULL,resolved_ns=NULL,
                    triage=NULL,analyzed_at=NULL WHERE id=%s''', (incident_id,))
                conn.execute("INSERT INTO audit(incident_id,actor,action) VALUES (%s,'collector','reopened')", (incident_id,))
            conn.execute('''UPDATE incidents SET occurrences=occurrences+1,
                first_ns=least(first_ns,%s), last_ns=greatest(last_ns,%s) WHERE id=%s''',
                         (ts, ts, incident_id))
            grouped.setdefault(incident_id, []).append(event_id)
    for incident_id, ids in grouped.items():
        item = conn.execute('SELECT * FROM incidents WHERE id=%s', (incident_id,)).fetchone()
        samples = evidence(conn, incident_id)
        conn.execute('UPDATE incidents SET saved_evidence=%s WHERE id=%s', (Jsonb(samples), incident_id))
        # Refresh an unstarted job with current examples; never alter an in-flight model request.
        conn.execute("""UPDATE jobs SET evidence=%s WHERE incident_id=%s AND generation=%s
            AND status='pending' AND attempts=0 AND result IS NULL""",
            (Jsonb({'examples': samples}), incident_id, item['generation']))
        conn.execute('''INSERT INTO jobs(id,incident_id,generation,evidence) VALUES (%s,%s,%s,%s)
            ON CONFLICT DO NOTHING''', (digest([incident_id, item['generation']]), incident_id,
                                        item['generation'], Jsonb({'examples': samples})))
        if retriage_due(item):
            conn.execute("""INSERT INTO jobs(id,incident_id,generation,evidence)
                SELECT %s,%s,%s,%s WHERE NOT EXISTS (
                    SELECT 1 FROM jobs WHERE incident_id=%s AND generation=%s AND status IN ('pending','running'))
                ON CONFLICT DO NOTHING""",
                (digest(['growth', incident_id, item['generation'], str(item['analyzed_at'])]), incident_id,
                 item['generation'], Jsonb({'examples': samples}), incident_id, item['generation']))
    return sum(map(len, grouped.values()))


def retriage_due(item):
    previous = (item['triage'] or {}).get('input_occurrences')
    return bool(item['status'] in ('review','observing','ready') and previous and item['analyzed_at']
        and (datetime.now(timezone.utc) - item['analyzed_at']).total_seconds() >= 900
        and item['occurrences'] >= max(5, 2 * previous))


def evidence(conn, incident_id):
    samples = conn.execute('''SELECT id, ts_ns::text, labels, message FROM events
        WHERE incident_id=%s ORDER BY ts_ns DESC LIMIT 3''', (incident_id,)).fetchall()
    if samples:
        return samples
    item = conn.execute('SELECT saved_evidence FROM incidents WHERE id=%s', (incident_id,)).fetchone()
    return item['saved_evidence'] if item else []
