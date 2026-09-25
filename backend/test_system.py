"""Runnable integration check. Uses a disposable PostgreSQL schema and real Loki.
Provider calls are mocked; no AI credentials, charges, or remote actions are used.
"""
import json
import os
import time
import uuid
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb

import app as lev
import core
import worker
from app import app


def run():
    for attempt in range(40):
        try:
            if httpx.get(core.LOKI + '/ready', timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Isolated test Loki did not become ready')
    schema = 'check_' + uuid.uuid4().hex
    with core.db() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    os.environ['PGOPTIONS'] = '-c search_path=' + schema
    os.environ['TYPESAFE_API_KEY'] = 'test-only'
    os.environ['AI_MODEL'] = ''
    calls = []
    try:
        core.initialize()
        key = lambda message: core.fingerprint({'labels': {'host': 'h'}, 'message': message})[0]
        # Grouping: rotating sources/ephemeral ports, Docker logfmt timestamps and hex IDs do not fragment incidents.
        assert key('[UFW BLOCK] IN=eth0 OUT= SRC=2a02::1 DST=192.0.2.9 LEN=60 PROTO=UDP SPT=5353 DPT=40001') == \
            key('[UFW BLOCK] IN=eth0 OUT= SRC=2a02::2 DST=192.0.2.9 LEN=90 PROTO=UDP SPT=5353 DPT=51234')
        assert key('[UFW BLOCK] IN=eth0 SRC=a DST=b PROTO=TCP DPT=22') != key('[UFW BLOCK] IN=eth0 SRC=a DST=b PROTO=TCP DPT=5432')
        assert key('time="2026-09-24T14:46:39.46+02:00" level=warning msg="healthcheck failed" actualDuration="866.1µs" container=8ae0fa149364c2f2') == \
            key('time="2026-09-24T14:49:44.09+02:00" level=warning msg="healthcheck failed" actualDuration="335.5µs" container=0123456789abcdef')
        assert key('lookup failed for DNS') != key('lookup failed for TCP')
        # Saturated ranges split on Loki's [start, end) boundaries without skipping the midpoint.
        stored = [5] * 4000 + [10] * 3000
        fake = lambda query, start, end, limit, direction: [{'ts_ns': t} for t in stored if start <= t < end][:limit]
        with patch.object(core, 'logs', side_effect=fake):
            assert len(core.complete_logs('q', 0, 20)) == 7000
            stored = [10] * 5000
            try:
                core.complete_logs('q', 0, 20)
                raise AssertionError('An unsplittable saturated nanosecond must hold the checkpoint')
            except RuntimeError:
                pass
        with core.db() as conn:
            old = {'host': 'fw-host', 'service': 'firewall'}
            for n, spt in enumerate(('1', '2')):
                conn.execute("INSERT INTO incidents(id,labels,pattern,first_ns,last_ns,occurrences,status) VALUES (%s,%s,%s,1,2,1,'review')",
                             ('old' + spt, core.Jsonb(old), f'[UFW BLOCK] IN=eth0 SRC=a{spt} DST=b PROTO=UDP SPT={spt} DPT=50000'))
            core.consolidate(conn)
            merged = conn.execute('SELECT superseded_by FROM incidents WHERE id IN (%s,%s)', ('old1', 'old2')).fetchall()
            assert len({m['superseded_by'] for m in merged}) == 1 and merged[0]['superseded_by']
            assert conn.execute('SELECT occurrences FROM incidents WHERE id=%s', (merged[0]['superseded_by'],)).fetchone()['occurrences'] == 2
            conn.execute("DELETE FROM jobs WHERE incident_id=%s", (merged[0]['superseded_by'],))
            conn.execute("DELETE FROM audit")
            conn.execute("DELETE FROM incidents")
        ts = time.time_ns() - 65 * core.NS
        labels = {'host': schema, 'server_id': schema, 'project_id': 'lev-test', 'service': 'checkout', 'environment': 'test'}
        raw = json.dumps({'message': 'database connection refused request_id=one', 'level': 'error'})
        response = httpx.post(core.LOKI + '/loki/api/v1/push', json={'streams': [{'stream': labels, 'values': [[str(ts), raw]]}]})
        response.raise_for_status()
        rows = core.logs(core.selector(labels), ts, ts + 1)
        assert len(rows) == 1, rows
        assert rows[0]['labels']['project_id'] == 'lev-test'
        assert rows[0]['labels']['server_id'] == schema
        assert 'project_id="lev-test"' in core.selector({'project_id': 'lev-test'})
        incident_id = core.fingerprint(rows[0])[0]
        with core.db() as conn:
            assert worker.ingest(conn, rows) == 1
            assert worker.ingest(conn, rows) == 0
            assert conn.execute('SELECT occurrences FROM incidents').fetchone()['occurrences'] == 1
            assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1
        assert core.fingerprint(rows[0])[0] == core.fingerprint({**rows[0], 'message': rows[0]['message'].replace('one', 'two')})[0]
        assert core.fingerprint({**rows[0], 'message': 'HTTP 401'})[0] != core.fingerprint({**rows[0], 'message': 'HTTP 500'})[0]
        assert '\\"' in core.selector({'service':'x"} |= "escape'})
        assert core.selector({}, '', 'refused "pool exhausted" NOT timeout') == '{service=~".+"} |= "refused" |= "pool exhausted" != "timeout"'

        # Fail the provider; ingestion still works and the checkpoint advances.
        with patch.object(worker.httpx, 'post', side_effect=httpx.ConnectError('provider offline')):
            worker.analyze_one()
        with core.db() as conn:
            job = conn.execute('SELECT * FROM jobs').fetchone()
            assert job['status'] == 'pending' and job['attempts'] == 1
            second = {**rows[0], 'ts_ns': str(ts+2)}
            assert worker.ingest(conn, [second]) == 1
            assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1
            conn.execute("UPDATE jobs SET next_attempt=now()")
        with patch.object(worker, 'complete_logs', return_value=[]):
            worker.collect()
        with core.db() as conn:
            assert conn.execute("SELECT checkpoint_ns FROM worker_state WHERE name='collector'").fetchone()['checkpoint_ns'] > 0

        def provider(url, **kwargs):
            calls.append(kwargs['json'])
            assert url.endswith('/v1/systemone')
            assert set(kwargs['json']) == {'model', 'state', 'questions'}
            answers = {}
            for key, question in kwargs['json']['questions'].items():
                choice = 'database' if key == 'category' else 'investigate'
                answers[key] = {'type':'choice', 'choice':choice, 'confidence':.95,
                                'probabilities': {option: float(option==choice) for option in question['criteria']}}
            return httpx.Response(200, request=httpx.Request('POST', url), json={'model':'test-jev', 'answers':answers, 'usage':{}})

        # A stale running job is recovered after a worker crash; completion is stored once.
        with core.db() as conn:
            conn.execute("UPDATE jobs SET status='running'")
        with patch.object(worker.httpx, 'post', side_effect=provider):
            assert worker.analyze_one()
            assert not worker.analyze_one()
        assert len(calls) == 1
        # Policy 1 is seeded from the built-in defaults, so upgrades triage exactly as before.
        assert calls[0]['model'] == worker.DEFAULT_POLICY['model']
        assert calls[0]['questions']['category']['criteria'] == worker.CATEGORIES
        with core.db() as conn:
            assert conn.execute('SELECT triage FROM incidents').fetchone()['triage']['policy_version'] == 1
            assert conn.execute('SELECT status FROM incidents').fetchone()['status'] == 'ready'
            assert conn.execute('SELECT status FROM jobs').fetchone()['status'] == 'done'

        agent = {'Authorization': 'Bearer ' + core.secret('agent_token')}
        with TestClient(app) as client:
            # First run: only the logged setup code creates the one admin; then login/logout work.
            assert client.get('/api/auth').json() == {'setup_required': True, 'user': None}
            new_admin = {'code': lev.setup_code, 'username': 'test-operator', 'password': 'correct horse battery'}
            assert client.post('/api/auth/setup', json=new_admin).status_code == 403, 'Setup needs the CSRF header'
            csrf = {'X-Lev-Request': '1'}
            assert client.post('/api/auth/setup', headers=csrf, json={**new_admin, 'code': 'wrong'}).status_code == 403
            assert client.post('/api/auth/setup', headers=csrf, json={**new_admin, 'password': 'short'}).status_code == 422
            created = client.post('/api/auth/setup', headers=csrf, json=new_admin)
            assert created.status_code == 200 and 'httponly' in created.headers['set-cookie'].lower(), created.text
            assert client.post('/api/auth/setup', headers=csrf, json={**new_admin, 'username': 'second'}).status_code == 409
            assert client.get('/api/auth').json() == {'setup_required': False, 'user': 'test-operator'}
            assert client.post('/api/auth/logout', headers=csrf).status_code == 200
            client.cookies.clear()
            assert client.get('/api/status', headers={'Cookie': lev.COOKIE + '=' + created.cookies[lev.COOKIE]}).status_code == 401, 'Logged-out session still valid'
            assert client.post('/api/auth/login', headers=csrf, json={'username': 'test-operator', 'password': 'wrong password'}).status_code == 401
            assert client.post('/api/auth/login', headers=csrf, json={'username': 'nobody', 'password': 'correct horse battery'}).status_code == 401
            token = client.post('/api/auth/login', headers=csrf, json={'username': 'test-operator', 'password': 'correct horse battery'}).cookies[lev.COOKIE]
            client.cookies.clear()
            admin = {'Cookie': lev.COOKIE + '=' + token, 'X-Lev-Request': '1'}
            assert client.get('/api/connect', headers=admin).json()['agent_token'] == core.secret('agent_token')
            assert client.get('/api/settings').status_code == 401
            saved = client.post('/api/settings', headers=admin, json={'AI_API_KEY': 'sk-ui', 'AI_MODEL': 'm1'}).json()
            assert saved['AI_API_KEY'] == {'secret': True, 'set': True} and 'sk-ui' not in json.dumps(saved), 'Secret leaked'
            assert core.setting('AI_API_KEY') == 'sk-ui' and core.setting('AI_MODEL') == 'm1'
            assert client.post('/api/settings', headers=admin, json={'AI_MODEL': ''}).json()['AI_MODEL'] == {'value': ''}, 'Clearing falls back to env'
            assert client.post('/api/settings', headers=admin, json={'BIND_ADDRESS': 'x'}).status_code == 422
            client.post('/api/settings', headers=admin, json={'AI_API_KEY': ''})
            assert client.get('/api/status').status_code == 401
            assert client.get('/api/status', headers={'X-Authenticated-User': 'forged'}).status_code == 401
            assert client.get('/api/agent/tasks', headers=admin).status_code == 401
            assert client.get('/api/status', headers=agent).status_code == 401
            assert client.get('/api/incidents', headers=admin).json() == [], 'Test traffic leaked into the operational list'
            assert client.get('/api/status', headers=admin).json()['incidents'] == 0
            # Jev page: pause blocks the analyzer, retry requeues failed jobs, control needs the CSRF header.
            assert client.post('/api/jev', headers={'Cookie': admin['Cookie']}, json={'action':'pause'}).status_code == 403
            assert client.post('/api/jev', headers=admin, json={'action':'pause'}).status_code == 200
            with core.db() as conn:
                conn.execute("UPDATE jobs SET status='failed'")
            assert client.post('/api/jev', headers=admin, json={'action':'retry_failed'}).json() == {'changed': 1}
            assert not worker.analyze_one()
            page = client.get('/api/jev', headers=admin).json()
            assert page['paused'] and page['queue'][0]['status'] == 'pending' and not page['jobs'], page
            assert client.post('/api/jev', headers=admin, json={'action':'resume'}).status_code == 200
            with core.db() as conn:
                conn.execute("UPDATE jobs SET status='done'")

            # Policy versions: validated, immutable, activated from the admin; rollback = activate an older id.
            assert client.get('/api/jev/policy', headers=admin).json()['active'] == 1
            tuned = json.loads(json.dumps(worker.DEFAULT_POLICY))
            tuned.update(model='jev-tuned', thresholds={'investigate': .99, 'observe': .6})
            tuned['categories']['storage'] = {'what': 'Disk and filesystem failures', 'not_for': 'Memory pressure', 'examples': ['No space left on device']}
            broken = {**tuned, 'categories': {'app': 'x', 'db': 'y'}}
            assert client.post('/api/jev/policy', headers=admin, json={'config': broken}).status_code == 422
            assert client.post('/api/jev/policy', headers=admin, json={'config': {**tuned, 'actionability': {'go': 'x', 'unknown': 'y'}}}).status_code == 422
            assert client.post('/api/jev/policy', headers={'Cookie': admin['Cookie']}, json={'config': tuned}).status_code == 403
            assert client.post('/api/jev/policy', headers=admin, json={'config': tuned, 'note': 'stricter'}).json() == {'id': 2, 'active': True}
            assert client.get('/api/jev', headers=admin).json()['settings']['policy_version'] == 2
            assert 'storage' in client.get('/api/status', headers=admin).json()['categories']
            assert client.post('/api/jev/policy/99/activate', headers=admin).status_code == 404

            # Labels are the tuning ground truth; insight re-routes stored answers under candidate gates for free.
            label_route = '/api/incidents/' + incident_id + '/label'
            assert client.post(label_route, headers=admin, json={'route': 'observing', 'category': 'bogus'}).status_code == 422
            assert client.post(label_route, headers=admin, json={'route': 'observing', 'category': 'database'}).status_code == 200
            detail = client.get('/api/incidents/' + incident_id, headers=admin).json()
            assert detail['label']['route'] == 'observing' and detail['audit'][0]['action'] == 'labelled'
            with core.db() as conn:
                assert conn.execute('SELECT evidence FROM labels').fetchone()['evidence']['examples'], 'Label needs an evidence snapshot'
                conn.execute('''UPDATE incidents SET labels=labels||'{"project_id":"routing","host":"routing"}' ''')
            strict = client.get('/api/jev/insight', headers=admin).json()  # active gate .99 > stored .95
            assert strict['thresholds']['investigate'] == .99 and strict['confusion'] == {'observing': {'review': 1}}, strict
            loose = client.get('/api/jev/insight', headers=admin, params={'investigate': .9}).json()
            assert loose['confusion'] == {'observing': {'ready': 1}} and loose['false_ready'] == 1 and loose['route_accuracy'] == 0, loose
            assert loose['category_accuracy'] == 1 and loose['labelled'] == 1 and not loose['to_label']
            assert client.post(label_route, headers=admin, json={'route': None}).status_code == 200
            assert client.get('/api/jev/insight', headers=admin, params={'investigate': .9}).json()['to_label'][0]['id'] == incident_id

            # Replay: a version is judged on labelled evidence only when no triage is due; incidents stay untouched.
            assert client.post(label_route, headers=admin, json={'route': 'observing', 'category': 'database'}).status_code == 200
            assert client.post('/api/jev/policy/2/replay', headers=admin, json={'include_active': False}).json() == {'queued': 1}
            with core.db() as conn:
                before = conn.execute('SELECT status,triage FROM incidents').fetchone()
                conn.execute("UPDATE jobs SET status='pending',next_attempt=now()")
            assert not worker.replay_one(), 'Replay must yield to real triage'
            with core.db() as conn:
                conn.execute("UPDATE jobs SET status='done'")
            with patch.object(worker.httpx, 'post', side_effect=provider):
                assert worker.replay_one() and not worker.replay_one()
            assert calls[-1]['model'] == 'jev-tuned' and calls[-1]['state']['evidence']['examples']
            with core.db() as conn:
                assert conn.execute('SELECT status,triage FROM incidents').fetchone() == before
            compared = client.get('/api/jev/policy/2/replay', headers=admin, params={'against': 1}).json()
            assert compared['progress'] == {'total': 1, 'done': 1, 'errors': 0} and compared['base_source'] == {'stored': 1}, compared
            assert compared['draft']['confusion'] == {'observing': {'review': 1}} and compared['base']['false_ready'] == 1, compared

            # AI suggestions use the Settings model, and only valid edits that keep the injection guard survive.
            draft_config = client.get('/api/jev/policy', headers=admin).json()['versions'][0]['config']
            assert client.post('/api/jev/suggest', headers=admin, json=draft_config).status_code == 422, 'Needs an AI model'
            client.post('/api/settings', headers=admin, json={'AI_MODEL': 'm1'})
            def ai(url, **kwargs):
                assert url.endswith('/chat/completions') and kwargs['json']['model'] == 'm1'
                assert json.loads(kwargs['json']['messages'][1]['content'])['misjudged'][0]['expected_route'] == 'observing'
                content = json.dumps({'suggestions': [
                    {'field': 'categories.database', 'value': {'what': 'Database failures', 'not_for': 'Network', 'examples': ['connection refused']}, 'reason': 'r'},
                    {'field': 'categories.madeup', 'value': 'x', 'reason': 'unknown option'},
                    {'field': 'instructions.category', 'value': 'Categorize everything quickly.', 'reason': 'drops the guard'}]})
                return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': content}}]})
            with patch.object(worker.httpx, 'post', side_effect=ai):
                suggested = client.post('/api/jev/suggest', headers=admin, json=draft_config).json()
            assert suggested['misjudged'] == 1 and suggested['uncertain'] == 0 and [s['field'] for s in suggested['suggestions']] == ['categories.database'], suggested
            # A full endpoint URL in Settings is not doubled (was: .../chat/completions/chat/completions -> 404).
            for base in ('https://ai.example/v1', 'https://ai.example/v1/chat/completions/'):
                client.post('/api/settings', headers=admin, json={'AI_BASE_URL': base})
                with patch.object(worker.httpx, 'post', side_effect=ai) as sent:
                    worker.chat_json('s', {'misjudged': [{'expected_route': 'observing'}]}, 'k', 5)
                assert sent.call_args.args[0] == 'https://ai.example/v1/chat/completions', sent.call_args
            client.post('/api/settings', headers=admin, json={'AI_MODEL': '', 'AI_BASE_URL': ''})
            with core.db() as conn:
                conn.execute('UPDATE incidents SET labels=labels||%s', (Jsonb({'project_id':'lev-test','host':schema}),))
            problem_list = client.get('/api/incidents?samples=true', headers=admin)
            assert problem_list.status_code == 200, problem_list.text
            assert len(problem_list.json()) == 1
            assert client.post('/api/analyze', headers={'Cookie': admin['Cookie']}).status_code == 403
            task = client.get('/api/agent/tasks/' + incident_id, headers=agent).json()
            assert task['permissions']['approved_proposal'] is None
            assert len(task['evidence']) == 2
            with core.db() as conn:  # Temporarily not a sample so the agent list shows it.
                conn.execute('''UPDATE incidents SET labels=labels||'{"project_id":"routing","host":"routing"}' ''')
            assert [t['id'] for t in client.get('/api/agent/tasks', headers=agent, params={'server_id': schema}).json()] == [incident_id]
            assert client.get('/api/agent/tasks', headers=agent, params={'server_id': 'other-server'}).json() == []
            with core.db() as conn:
                conn.execute('UPDATE incidents SET labels=labels||%s', (Jsonb({'project_id':'lev-test','host':schema}),))
            route = '/api/agent/tasks/' + incident_id
            dismissal = {'generation':1,'request_id':'test-dismissal','reason':'Expected noise from image builds.'}
            assert client.post(route+'/dismiss', headers=agent, json={**dismissal,'generation':2}).status_code == 409
            assert client.post(route+'/dismiss', headers=agent, json=dismissal).json() == {'status':'observing'}
            assert client.post(route+'/dismiss', headers=agent, json=dismissal).json() == {'status':'observing'}
            assert client.post(route+'/dismiss', headers=agent, json={**dismissal,'request_id':'test-dismissal-2'}).status_code == 409
            assert client.post('/api/incidents/'+incident_id+'/dismiss', headers=agent, json=dismissal).status_code == 401
            assert client.get('/api/incidents/'+incident_id, headers=admin).json()['audit'][0]['data']['reason'] == dismissal['reason']
            with core.db() as conn:
                conn.execute("UPDATE incidents SET status='ready'")
            proposal = {'generation':1,'request_id':'test-proposal','diagnosis':'Connection configuration is incorrect.',
                        'changes':['Correct the database endpoint in checkout configuration.'],
                        'checks':['Checkout can connect to the database.'],
                        'rollback':'Restore the previous configuration file and reload checkout.', 'risk':'low'}
            route = '/api/agent/tasks/' + incident_id
            first = client.post(route+'/proposal', headers=agent, json=proposal)
            assert first.status_code == 200, first.text
            assert client.post(route+'/proposal', headers=agent, json=proposal).json() == first.json()
            approval = {'generation':1,'proposal_id':first.json()['id']}
            op = {**dismissal,'request_id':'test-operator-dismissal'}
            assert client.post('/api/incidents/'+incident_id+'/dismiss', headers=admin, json=op).json() == {'status':'observing'}
            assert client.get('/api/incidents/'+incident_id, headers=admin).json()['audit'][0]['actor'] == 'test-operator'
            with core.db() as conn:
                conn.execute("UPDATE incidents SET status='ready'")
            assert client.post('/api/incidents/'+incident_id+'/dismiss', headers=admin, json={'generation':1,'request_id':'test-operator-quick','reason':' '}).json() == {'status':'observing'}
            assert client.get('/api/incidents/'+incident_id, headers=admin).json()['audit'][0]['data']['reason'] == 'Human operator decision'
            with core.db() as conn:
                conn.execute("UPDATE incidents SET status='proposed'")
            verify = {**approval,'request_id':'test-verification','changes_applied':'Corrected the endpoint and reloaded checkout.',
                      'checks':[{'check':proposal['checks'][0], 'passed':True,'evidence':'Probe returned successful connection.'}]}
            assert client.post(route+'/verify', headers=agent, json=verify).status_code == 409
            assert client.post('/api/incidents/'+incident_id+'/approve', headers=agent, json=approval).status_code == 401
            assert client.post('/api/incidents/'+incident_id+'/approve', headers=admin, json=approval).status_code == 200
            assert client.post('/api/incidents/'+incident_id+'/dismiss', headers=admin, json={**op,'request_id':'test-operator-late'}).status_code == 409
            assert client.post(route+'/verify', headers=agent, json={**verify,'checks':[{'check':'Wrong check','passed':True,'evidence':'ok!'}]}).status_code == 422
            result = client.post(route+'/verify', headers=agent, json=verify)
            assert result.status_code == 200 and result.json()['status'] == 'verifying', result.text
            assert client.post(route+'/verify', headers=agent, json=verify).json() == result.json()

            # No heartbeat => no resolution. A healthy source plus passed checks can resolve.
            with core.db() as conn:
                conn.execute("UPDATE incidents SET verification_ns=%s", (time.time_ns()-1000*core.NS,))
            with patch.object(worker, 'complete_logs', return_value=[]), patch.object(worker, 'logs', return_value=[]):
                worker.collect()
            with core.db() as conn:
                assert conn.execute('SELECT status FROM incidents').fetchone()['status'] == 'verifying'
            with patch.object(worker, 'complete_logs', return_value=[]), patch.object(worker, 'logs', return_value=[{'heartbeat':True}]):
                worker.collect()
            with core.db() as conn:
                assert conn.execute('SELECT status FROM incidents').fetchone()['status'] == 'resolved'
                # A new occurrence reopens the problem, invalidating the old approval.
                core.ingest(conn, [{**rows[0], 'ts_ns':str(time.time_ns())}])
                item = conn.execute('SELECT * FROM incidents').fetchone()
                assert item['status'] == 'new' and item['generation'] == 2 and item['proposal'] is None
                conn.execute('DELETE FROM events')
                assert core.evidence(conn, incident_id), 'Retained evidence must survive raw-log expiry'
            assert client.post(route+'/verify', headers=agent, json=verify).status_code == 409
            body = {'incident_id':incident_id,'request_id':'same-manual-retry'}
            assert client.post('/api/analyze', headers=admin,json=body).json() == client.post('/api/analyze',headers=admin,json=body).json()
            # The next triage uses the active policy's model, criteria and gates (.95 < .99 -> review).
            with patch.object(worker.httpx, 'post', side_effect=provider):
                while worker.analyze_one():
                    pass
            assert calls[-1]['model'] == 'jev-tuned' and 'storage' in calls[-1]['questions']['category']['criteria']
            with core.db() as conn:
                item = conn.execute('SELECT status,triage FROM incidents').fetchone()
                assert item['status'] == 'review' and item['triage']['policy_version'] == 2, item['status']
            assert client.post('/api/jev/policy/1/activate', headers=admin).json() == {'id': 1, 'active': True}
            # A retry keeps its classification's policy (2, not the now-active 1), and an explanation outage falls back.
            client.post('/api/settings', headers=admin, json={'AI_MODEL': 'm1'})
            with core.db() as conn:
                conn.execute("UPDATE incidents SET status='new'")
                conn.execute('''UPDATE jobs SET status='pending',next_attempt=now() WHERE id=(SELECT id FROM jobs
                    WHERE result->'triage'->>'policy_version'='2' ORDER BY completed_at DESC LIMIT 1)''')
            with patch.object(worker.httpx, 'post', side_effect=httpx.ConnectError('explanation offline')):
                assert worker.analyze_one()
            with core.db() as conn:
                item = conn.execute('SELECT status,triage,summary FROM incidents').fetchone()
                assert item['status'] == 'review' and item['triage']['policy_version'] == 2 and item['summary'], item
                assert conn.execute("SELECT count(*) AS n FROM jobs WHERE status='done' AND error LIKE 'Explanation fell back%%'").fetchone()['n'] == 1
            assert client.get('/api/jev', headers=admin).json()['settings']['triage_confidence'] == .8
            assert client.get('/api/logs',headers=admin,params={'start':ts,'end':ts+49*3600*core.NS}).status_code == 422
            source = next(s for s in client.get('/api/sources',headers=admin).json()['sources'] if s['server_id'] == schema)
            assert source['services'] == {'checkout': 1} and source['last_heartbeat_ns'] is None, source
            assert client.get('/api/sources').status_code == 401
            assert schema in client.get('/api/labels',headers=admin).json()['server_id']
        print('PASS: first-run setup, sessions, Loki ingestion, deduplication, provider outage, checkpoints, Jev contract, crash recovery, auth, proposal approval, verification, recurrence, retained evidence and manual idempotency.')
    finally:
        os.environ.pop('PGOPTIONS', None)
        with core.db() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    run()
