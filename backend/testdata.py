"""Realistic demo logs (Settings page): pushed to Loki and ingested exactly as the collector would, plus the full wipe."""
from datetime import datetime, timedelta, timezone
import json
import random
import time
import uuid

import httpx

from core import LOKI, NS

HOSTS = {'web-01': ('webshop', 'production'), 'app-01': ('webshop', 'production'), 'db-01': ('webshop', 'production'),
         'worker-01': ('billing', 'production'), 'edge-01': ('host-infrastructure', 'production'),
         'stg-app-01': ('webshop', 'staging')}
POOL_TIMEOUT = '''request_id={} POST /api/checkout failed
Traceback (most recent call last):
  File "/app/checkout/views.py", line 142, in create_order
    with db.transaction() as tx:
  File "/usr/local/lib/python3.12/site-packages/psycopg_pool/pool.py", line 202, in connection
    conn = self.getconn(timeout=timeout)
  File "/usr/local/lib/python3.12/site-packages/psycopg_pool/pool.py", line 239, in getconn
    raise PoolTimeout(
psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec'''


def generate(now=None):
    now = now or time.time_ns()
    # Loki drops lines over an hour older than a stream's newest, so everything fits in 55 minutes and reloads still land.
    start, end = now - 55 * 60 * NS, now - 60 * NS
    storm = now - 40 * 60 * NS  # 10-minute database connection storm that cascades up the stack
    rows = []

    def emit(count, host, service, level, make, lo=start, hi=end):
        project, environment = HOSTS[host]
        labels = {'host': host, 'server_id': host, 'project_id': project, 'service': service, 'environment': environment}
        for _ in range(count):
            ts, message = random.randint(lo, hi), make()
            stamp = datetime.fromtimestamp(ts / NS, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
            raw = json.dumps({'timestamp': stamp, 'message': message, 'level': level, **labels,
                              'source': 'journald', 'process_id': str(random.randint(900, 60000))})
            rows.append({'ts_ns': str(ts), 'labels': labels, 'message': message, 'level': level, 'raw': raw})

    pid = lambda: f'pid={random.randint(20000, 90000)} db=shop user=checkout '
    order = lambda: f'order_id=ORD-{random.randint(100000, 999999)}'
    storm_window = {'lo': storm, 'hi': storm + 10 * 60 * NS}
    # The storm: postgres runs out of connections, the API pool times out, nginx returns 504s.
    emit(40, 'db-01', 'postgresql', 'error', lambda: pid() + 'FATAL:  sorry, too many clients already', **storm_window)
    emit(12, 'db-01', 'postgresql', 'error', lambda: pid() + 'ERROR:  canceling statement due to statement timeout\n'
         'STATEMENT:  SELECT o.id, sum(l.amount) FROM orders o JOIN order_lines l ON l.order_id = o.id WHERE o.customer_id = $1 GROUP BY o.id', **storm_window)
    emit(60, 'app-01', 'checkout-api', 'error', lambda: POOL_TIMEOUT.format(uuid.uuid4()), **storm_window)
    emit(80, 'web-01', 'nginx', 'error', lambda: 'upstream timed out (110: Connection timed out) while reading response header from upstream, '
         f'server: shop.example.com, request: "POST /api/checkout HTTP/2.0", upstream: "http://10.0.1.12:8000/api/checkout", request_id={uuid.uuid4().hex}', **storm_window)
    # Steady background problems.
    emit(7, 'db-01', 'postgresql', 'error', lambda: pid() + 'ERROR:  deadlock detected\nHINT:  See server log for query details.\n'
         'STATEMENT:  UPDATE inventory SET reserved = reserved + $1 WHERE sku = $2')
    emit(34, 'app-01', 'checkout-api', 'warn', lambda: f'Payment provider responded 429 Too Many Requests {order()}; retrying in {random.choice([1, 2, 4])}s (attempt 2/3)')
    emit(4, 'app-01', 'checkout-api', 'error', lambda: f'{order()} payment capture failed: provider returned 502 Bad Gateway after 3 retries')
    emit(1, 'app-01', 'kernel', 'error', lambda: 'Out of memory: Killed process 23817 (gunicorn) total-vm:2489320kB, anon-rss:1843212kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:4212kB oom_score_adj:0',
         lo=now - 16 * 60 * NS, hi=now - 15 * 60 * NS)
    emit(1, 'app-01', 'systemd', 'warn', lambda: 'checkout-api.service: Main process exited, code=killed, status=9/KILL',
         lo=now - 15 * 60 * NS, hi=now - 14 * 60 * NS)
    # The invoice disk fills up during the last half hour.
    emit(6, 'worker-01', 'invoice-worker', 'warn', lambda: 'Disk usage on /var/lib/invoices above 90% threshold', lo=now - 35 * 60 * NS)
    emit(18, 'worker-01', 'invoice-worker', 'error', lambda: f'request_id={uuid.uuid4()} render_invoice failed\nTraceback (most recent call last):\n'
         '  File "/srv/billing/render.py", line 88, in render_invoice\n    pdf.write(path)\n'
         f"OSError: [Errno 28] No space left on device: '/var/lib/invoices/tmp/{uuid.uuid4().hex}.pdf'", lo=now - 25 * 60 * NS)
    # Noise that should be observed rather than fixed.
    for port, count in ((22, 90), (3389, 40), (23, 25), (5432, 8)):
        emit(count, 'edge-01', 'kernel', 'warn', lambda port=port: f'[UFW BLOCK] IN=eth0 OUT= MAC=52:54:00:9a:1c:07:fe:ff:ff:ff:ff:ff:08:00 '
             f'SRC={random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)} DST=203.0.113.10 LEN=44 '
             f'TOS=0x00 PREC=0x00 TTL={random.randint(40, 250)} ID={random.randint(1, 65535)} PROTO=TCP SPT={random.randint(1024, 65535)} DPT={port} WINDOW=1024 RES=0x00 SYN URGP=0')
    expiry = (datetime.fromtimestamp(now / NS, timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT09:14:00Z')
    emit(6, 'stg-app-01', 'checkout-api', 'warn', lambda: f'TLS certificate for staging.shop.example.com expires in 6 days (notAfter={expiry})')
    emit(22, 'stg-app-01', 'checkout-api', 'warn', lambda: 'DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version.')
    rows.sort(key=lambda row: int(row['ts_ns']))
    # Every host forwards a heartbeat each minute, as Vector does.
    beats = []
    for host, (project, environment) in HOSTS.items():
        labels = {'host': host, 'server_id': host, 'project_id': project, 'service': 'lev-heartbeat', 'environment': environment}
        for ts in range(start, now, 60 * NS):
            raw = json.dumps({'message': 'collector heartbeat', 'level': 'info', **labels})
            beats.append({'ts_ns': str(ts), 'labels': labels, 'message': 'collector heartbeat', 'level': 'info', 'raw': raw})
    return rows, beats


def push(rows):
    streams = {}
    for row in rows:
        streams.setdefault(json.dumps(row['labels'], sort_keys=True), []).append([row['ts_ns'], row['raw']])
    body = {'streams': [{'stream': json.loads(key), 'values': sorted(values, key=lambda v: int(v[0]))} for key, values in streams.items()]}
    response = httpx.post(LOKI + '/loki/api/v1/push', json=body, timeout=60)
    # Loki keeps valid lines and rejects ones it considers too old; report, don't fail.
    return '' if response.is_success else response.text[:300]


def wipe(conn):
    # Loki first: if it refuses, PostgreSQL is untouched. Deleted lines drop out of queries, disk space frees later.
    # Loki refuses an end in the future; anything newer than now arrives after the wipe anyway.
    now = int(time.time())
    response = httpx.post(LOKI + '/loki/api/v1/delete', params={
        'query': '{service=~".+"}', 'start': str(now - 49 * 3600), 'end': str(now)}, timeout=30)
    if not response.is_success:
        raise httpx.HTTPError(f'{response.status_code} {response.text.strip()[:200]}')
    conn.execute('SELECT pg_advisory_xact_lock(41001)')  # collector
    conn.execute('SELECT pg_advisory_xact_lock(41002)')  # triage
    conn.execute('TRUNCATE incidents, events, jobs, audit, labels, replays')
    conn.execute("UPDATE worker_state SET checkpoint_ns=%s WHERE name='collector'", (time.time_ns(),))
