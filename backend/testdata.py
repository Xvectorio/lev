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
    start = now - 55 * 60 * NS
    rows = []

    def emit(count, host, service, level, message, ago=(55, 1)):
        # message: text or a callable for per-line variation (ids, durations, IPs); ago: (from, to) minutes before now.
        project, environment = HOSTS[host]
        labels = {'host': host, 'server_id': host, 'project_id': project, 'service': service, 'environment': environment}
        for _ in range(count):
            ts = random.randint(now - ago[0] * 60 * NS, now - ago[1] * 60 * NS)
            text = message() if callable(message) else message
            stamp = datetime.fromtimestamp(ts / NS, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
            raw = json.dumps({'timestamp': stamp, 'message': text, 'level': level, **labels,
                              'source': 'journald', 'process_id': str(random.randint(900, 60000))})
            rows.append({'ts_ns': str(ts), 'labels': labels, 'message': text, 'level': level, 'raw': raw})

    pid = lambda: f'pid={random.randint(20000, 90000)} db=shop user=checkout '
    order = lambda: f'order_id=ORD-{random.randint(100000, 999999)}'
    request = lambda: f'request_id={uuid.uuid4()}'
    ip = lambda: f'{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}'
    storm = (40, 30)
    # Story: at -40 min postgres runs out of connections and it cascades up to nginx; at -20 someone rotates the
    # reporting password; the invoice disk fills and the mail relay's DNS breaks; at -12 a staging deploy lacks a secret.
    # Clear failures (ready), harmless noise (observing) and deliberately borderline cases (review) cover every category.

    # database
    emit(40, 'db-01', 'postgresql', 'error', lambda: pid() + 'FATAL:  sorry, too many clients already', storm)
    emit(12, 'db-01', 'postgresql', 'error', lambda: pid() + 'ERROR:  canceling statement due to statement timeout\n'
         'STATEMENT:  SELECT o.id, sum(l.amount) FROM orders o JOIN order_lines l ON l.order_id = o.id WHERE o.customer_id = $1 GROUP BY o.id', storm)
    emit(60, 'app-01', 'checkout-api', 'error', lambda: POOL_TIMEOUT.format(uuid.uuid4()), storm)
    emit(7, 'db-01', 'postgresql', 'error', lambda: pid() + 'ERROR:  deadlock detected\nHINT:  See server log for query details.\n'
         'STATEMENT:  UPDATE inventory SET reserved = reserved + $1 WHERE sku = $2')
    emit(15, 'db-01', 'postgresql', 'warn', lambda: pid() + 'WARNING:  there is no transaction in progress')
    emit(2, 'db-01', 'postgresql', 'warn', lambda: pid() + 'WARNING:  replication slot "standby_1" is 2143 MB behind; WAL is being retained', (8, 1))
    # dependency
    emit(80, 'web-01', 'nginx', 'error', lambda: 'upstream timed out (110: Connection timed out) while reading response header from upstream, '
         f'server: shop.example.com, request: "POST /api/checkout HTTP/2.0", upstream: "http://10.0.1.12:8000/api/checkout", request_id={uuid.uuid4().hex}', storm)
    emit(34, 'app-01', 'checkout-api', 'warn', lambda: f'Payment provider responded 429 Too Many Requests {order()}; retrying in {random.choice([1, 2, 4])}s (attempt 2/3)')
    emit(4, 'app-01', 'checkout-api', 'error', lambda: f'{order()} payment capture failed: provider returned 502 Bad Gateway after 3 retries')
    emit(20, 'worker-01', 'invoice-worker', 'warn', 'Exchange-rate API responded 503 Service Unavailable; using cached rates from 2 hours ago')
    # resources
    emit(30, 'web-01', 'nginx', 'error', 'accept4() failed (24: Too many open files)', (36, 31))
    emit(1, 'app-01', 'kernel', 'error', 'Out of memory: Killed process 23817 (gunicorn) total-vm:2489320kB, anon-rss:1843212kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:4212kB oom_score_adj:0', (16, 15))
    emit(1, 'app-01', 'systemd', 'warn', 'checkout-api.service: Main process exited, code=killed, status=9/KILL', (15, 14))
    emit(6, 'worker-01', 'invoice-worker', 'warn', 'Disk usage on /var/lib/invoices above 90% threshold', (35, 1))
    emit(18, 'worker-01', 'invoice-worker', 'error', lambda: f'{request()} render_invoice failed\nTraceback (most recent call last):\n'
         '  File "/srv/billing/render.py", line 88, in render_invoice\n    pdf.write(path)\n'
         f"OSError: [Errno 28] No space left on device: '/var/lib/invoices/tmp/{uuid.uuid4().hex}.pdf'", (25, 1))
    # authentication
    emit(25, 'db-01', 'postgresql', 'error', lambda: f'pid={random.randint(20000, 90000)} db=shop user=reporting FATAL:  password authentication failed for user "reporting"', (20, 1))
    emit(14, 'worker-01', 'invoice-worker', 'error', 'Token refresh for mail provider failed: 401 invalid_grant (refresh token has been revoked)', (30, 1))
    emit(9, 'app-01', 'checkout-api', 'error', lambda: f'{request()} rejected payment webhook: signature verification failed for key_id=whsec_live_2')
    emit(3, 'web-01', 'sudo', 'warn', 'pam_unix(sudo:auth): authentication failure; logname=deploy uid=1001 euid=0 tty=/dev/pts/0 ruser=deploy rhost=  user=deploy')
    # configuration
    emit(12, 'stg-app-01', 'checkout-api', 'fatal', 'Startup aborted: required setting STRIPE_WEBHOOK_SECRET is not set', (12, 1))
    emit(3, 'web-01', 'nginx', 'warn', 'conflicting server name "shop.example.com" on 0.0.0.0:443, ignored', (50, 45))
    emit(8, 'worker-01', 'invoice-worker', 'warn', lambda: f'Job invoice.generate exceeded soft time limit ({random.choice([60, 61, 63])}s); retrying')
    # network
    emit(11, 'worker-01', 'invoice-worker', 'error', 'SMTP connect to smtp.mailrelay.example.net:587 failed: [Errno -3] Temporary failure in name resolution', (18, 1))
    emit(5, 'app-01', 'checkout-api', 'warn', 'Redis connection reset by peer; reconnected after 1 attempt')
    emit(2, 'edge-01', 'kernel', 'warn', 'igb 0000:03:00.1 eth1: igb: eth1 NIC Link is Down', (27, 26))
    emit(3, 'edge-01', 'kernel', 'warn', 'TCP: request_sock_TCP: Possible SYN flooding on port 443. Sending cookies.  Check SNMP counters.', (34, 32))
    for port, count in ((22, 90), (3389, 40), (23, 25), (5432, 8)):
        emit(count, 'edge-01', 'kernel', 'warn', lambda port=port: f'[UFW BLOCK] IN=eth0 OUT= MAC=52:54:00:9a:1c:07:fe:ff:ff:ff:ff:ff:08:00 '
             f'SRC={ip()} DST=203.0.113.10 LEN=44 TOS=0x00 PREC=0x00 TTL={random.randint(40, 250)} ID={random.randint(1, 65535)} '
             f'PROTO=TCP SPT={random.randint(1024, 65535)} DPT={port} WINDOW=1024 RES=0x00 SYN URGP=0')
    expiry = (datetime.fromtimestamp(now / NS, timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT09:14:00Z')
    emit(6, 'stg-app-01', 'checkout-api', 'warn', f'TLS certificate for staging.shop.example.com expires in 6 days (notAfter={expiry})')
    # application
    emit(13, 'app-01', 'checkout-api', 'error', lambda: f'{request()} POST /api/checkout/guest failed\nTraceback (most recent call last):\n'
         '  File "/app/checkout/serializers.py", line 57, in to_internal_value\n    address = data["shipping_address"]\n'
         "KeyError: 'shipping_address'")
    emit(2, 'app-01', 'checkout-api', 'error', 'Order reconciliation mismatch: 3 orders in state PAID have no ledger entry', (22, 3))
    emit(22, 'stg-app-01', 'checkout-api', 'warn', 'DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version.')
    # unknown: too little to go on
    emit(2, 'web-01', 'systemd-journald', 'warn', 'Missed 23 kernel messages', (29, 28))
    emit(1, 'worker-01', 'invoice-worker', 'error', 'Unexpected state; giving up', (9, 8))
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
