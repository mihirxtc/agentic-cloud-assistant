"""Real queue/worker boundaries; fake AWS/runner, no external calls or sleep."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from services import jobs, job_worker, aws_connection as aws, runner_client
from services.jobs import Jobs, public
from services.action_identity import actor

@pytest.fixture
def queued(environment, monkeypatch):
    server, app, store, _ = environment
    monkeypatch.setattr(jobs, 'active_job', ContextVar('real_queue', default=None))
    monkeypatch.setattr(job_worker, 'active_job', jobs.active_job)
    value = aws.AWSConnection('111111111111', 'eu-west-1', 'arn:aws:iam::111111111111:user/test',
                              'aws', 'now', Mock(), 'secret-access', 'secret-key', 'secret-session')
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=value))
    monkeypatch.setattr(job_worker, 'verify_connection', aws.verify_connection)
    def terraform(command, **kwargs):
        for part in command:
            if part.startswith('-out='):
                (kwargs['cwd'] / part.split('=', 1)[1]).write_bytes(b'saved plan')
        return SimpleNamespace(returncode=0, stdout='Complete', stderr='')
    store.test_runner.run = Mock(side_effect=terraform)
    return server, app, store, value, Jobs(store)

def planned(env):
    job = asyncio.run(env[0].run_terraform_plan_mcp('provider "aws" {}', aws_region='eu-west-1', idempotency_key='plan-request'))
    assert job['status'] == 'queued'
    assert job_worker.run_once(env[-1])
    result = env[-1].get(job['job_id'])
    assert result['status'] == 'complete', result
    return result['result']

def approved(env):
    proposal = planned(env)
    result = env[0].agent_approve(proposal['execution_id'], True, approval_digest=proposal['approval_digest'], idempotency_key='approve-request')
    return proposal, result

def test_persistent_plan_and_no_saved_credentials(queued):
    server, _, store, _, queue = queued
    job = asyncio.run(server.run_terraform_plan_mcp('provider "aws" {}', aws_region='eu-west-1'))
    store.test_runner.run.assert_not_called()
    restarted = Jobs(store)
    assert restarted.get(job['job_id'])['status'] == 'queued'
    assert job_worker.run_once(restarted)
    result = restarted.get(job['job_id'])
    assert result['result']['status'] == 'awaiting_approval'
    assert result['execution_id'] == result['result']['execution_id']
    assert 'Saving reviewable proposal' in [e['stage'] for e in result['events']]
    assert result['events'][-1]['stage'] == 'Finished'
    assert b'secret-key' not in store.path.read_bytes()
    assert 'arguments' not in public(result) and 'actor' not in public(result)

def test_submission_idempotency_and_conflict(queued):
    args = {'aws_region': 'eu-west-1', 'idempotency_key': 'submission-key'}
    first = asyncio.run(queued[0].run_terraform_plan_mcp('provider "aws" {}', **args))
    assert asyncio.run(queued[0].run_terraform_plan_mcp('provider "aws" {}', **args))['job_id'] == first['job_id']
    with pytest.raises(ValueError, match='different content'):
        asyncio.run(queued[0].run_terraform_plan_mcp('provider "aws" {}', description='changed', **args))
    assert len(queued[-1].list()) == 1

def test_approval_queues_atomically_and_dispatches_once(queued):
    proposal, job = approved(queued)
    server, _, store, _, queue = queued
    store.test_runner.run.reset_mock()
    assert store.get(proposal['execution_id'])['job_id'] == job['job_id']
    assert server.agent_approve(proposal['execution_id'], True, approval_digest=proposal['approval_digest'], idempotency_key='approve-request')['job_id'] == job['job_id']
    store.test_runner.run.assert_not_called()
    assert job_worker.run_once(queue)
    assert store.get(proposal['execution_id'])['status'] == 'complete'
    assert queue.get(job['job_id'])['result']['status'] == 'complete'
    assert sum('apply' in c.args[0] for c in store.test_runner.run.call_args_list) == 1
    assert not job_worker.run_once(queue)

def test_bad_digest_rolls_back_job_and_action(queued):
    proposal = planned(queued)
    with pytest.raises(ValueError, match='digest'):
        queued[0].agent_approve(proposal['execution_id'], True, approval_digest='wrong', idempotency_key='bad-approve')
    assert len(queued[-1].list()) == 1
    assert queued[2].get(proposal['execution_id'])['status'] == 'awaiting_approval'

def test_competing_workers(queued):
    queue = queued[-1]
    queue.submit('full_aws_scan', {}, queued[3].identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(queue.claim, ['worker-one', 'worker-two']))
    assert sum(item is not None for item in claims) == 1

def test_cancel_only_queued_work(queued):
    queue = queued[-1]
    job = queue.submit('full_aws_scan', {}, queued[3].identity)
    assert queue.cancel(job['job_id'])['status'] == 'cancelled'
    assert queue.claim('worker') is None
    _, job = approved(queued)
    queue.claim('active-worker')
    with pytest.raises(ValueError, match='queued'):
        queue.cancel(job['job_id'])

def test_expired_read_job_is_fenced_and_not_retried(queued):
    queue = queued[-1]
    job = queue.submit('full_aws_scan', {}, queued[3].identity)
    queue.claim('dead-worker')
    queue.recover(time.time() + 120)
    assert queue.get(job['job_id'])['status'] == 'interrupted'
    assert queue.claim('new-worker') is None
    with pytest.raises(RuntimeError, match='lease'):
        queue.touch(job['job_id'], 'dead-worker')
    queue.finish(job['job_id'], 'dead-worker', result={'stale': True})
    assert 'result' not in queue.get(job['job_id'])

def test_expired_apply_retains_lock_and_never_replays(queued):
    proposal, job = approved(queued)
    queue, store = queued[-1], queued[2]
    queue.claim('dead-worker'); queue.recover(time.time() + 120)
    assert queue.get(job['job_id'])['status'] == 'reconciliation_required'
    assert store.get(proposal['execution_id'])['status'] == 'uncertain'
    with store._connection() as db:
        assert db.execute('SELECT execution_id FROM action_locks').fetchone()[0] == proposal['execution_id']
    assert not job_worker.run_once(queue)

def test_fresh_identity_required_before_worker_dispatch(queued, monkeypatch):
    job = asyncio.run(queued[0].run_terraform_plan_mcp('provider "aws" {}', aws_region='eu-west-1'))
    monkeypatch.setattr(job_worker, 'verify_connection', lambda _: replace(queued[3], account_id='222222222222'))
    job_worker.run_once(queued[-1])
    assert queued[-1].get(job['job_id'])['status'] == 'failed'
    queued[2].test_runner.run.assert_not_called()

def test_model_credentials_rejected_before_persistence(queued):
    with pytest.raises(ValueError, match='server model'):
        asyncio.run(queued[0].agent_run(region='eu-west-1', api_key='private-model-key'))
    assert queued[-1].list() == []

def test_submitter_actor_survives_worker_boundary(queued):
    token = actor.set({'operator': 'tester', 'kind': 'bearer'})
    try:
        proposal = planned(queued)
    finally:
        actor.reset(token)
    assert queued[2].action_events(proposal['execution_id'])[0]['actor'] == {'operator': 'tester', 'kind': 'bearer'}

def test_authenticated_http_submission_status_and_cancellation(queued):
    with TestClient(queued[1].create_app()) as client:
        assert client.get('/jobs').status_code == 401
        assert client.post('/jobs/missing/cancel').status_code == 401
        headers = {'Authorization': 'Bearer test-mcp-token'}
        response = client.post('/tools/run_terraform_plan_mcp', json={'hcl_config': 'provider "aws" {}', 'aws_region': 'eu-west-1'}, headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        assert job['status'] == 'queued'
        queued[2].test_runner.run.assert_not_called()
        assert client.get('/jobs', headers=headers).json()['jobs'][0]['job_id'] == job['job_id']
        assert client.get('/jobs/' + job['job_id'], headers=headers).json()['status'] == 'queued'
        assert client.get('/jobs/missing', headers=headers).status_code == 404
        assert client.post('/jobs/' + job['job_id'] + '/cancel', headers=headers).json()['status'] == 'cancelled'

def test_reconcile_only_reviewed_terminal_evidence(queued, monkeypatch):
    proposal, job = approved(queued)
    queue = queued[-1]
    queue.claim('dead'); queue.recover(time.time() + 120)
    monkeypatch.setattr(runner_client, 'operation', lambda *a, **k: {'dispatched': True, 'outcome': {'status': 'running'}})
    evidence = queued[0].inspect_interrupted_action(proposal['execution_id'])
    assert not evidence['recoverable']
    with pytest.raises(ValueError, match='locked'):
        queued[0].reconcile_action(proposal['execution_id'], evidence['evidence_digest'])
    monkeypatch.setattr(runner_client, 'operation', lambda *a, **k: {'dispatched': True, 'outcome': {'status': 'complete', 'result': {'success': True, 'apply_output': 'Complete'}}})
    with pytest.raises(ValueError, match='changed'):
        queued[0].reconcile_action(proposal['execution_id'], evidence['evidence_digest'])
    fresh = queued[0].inspect_interrupted_action(proposal['execution_id'])
    assert queued[0].reconcile_action(proposal['execution_id'], fresh['evidence_digest'])['status'] == 'complete'
    assert queue.get(job['job_id'])['status'] == 'complete'
    with queued[2]._connection() as db:
        assert db.execute('SELECT * FROM action_locks').fetchall() == []

def test_reconciliation_cannot_unlock_active_job(queued, monkeypatch):
    proposal, _ = approved(queued)
    monkeypatch.setattr(runner_client, 'operation', lambda *a, **k: {'dispatched': True, 'outcome': {'status': 'complete', 'result': {'success': True}}})
    evidence = queued[0].inspect_interrupted_action(proposal['execution_id'])
    with pytest.raises(ValueError, match='active'):
        queued[0].reconcile_action(proposal['execution_id'], evidence['evidence_digest'])

def test_recovery_restores_already_committed_completion(queued):
    proposal, job = approved(queued)
    queue, store = queued[-1], queued[2]
    queue.claim('lost-response')
    store.finish_action(proposal['execution_id'], 'complete', {'apply_output': 'Saved before crash'})
    queue.recover(time.time() + 120)
    restored = queue.get(job['job_id'])
    assert restored['status'] == 'complete'
    assert restored['result']['apply_output'] == 'Saved before crash'
    assert not job_worker.run_once(queue)


def test_worker_readiness_expires_without_heartbeat(queued, monkeypatch):
    queue = queued[-1]
    assert not queue.worker_ready()
    queue.worker_heartbeat('worker')
    assert queue.worker_ready()
    now = time.time()
    monkeypatch.setattr(jobs.time, 'time', lambda: now + 120)
    assert not queue.worker_ready()


def test_cache_invalidation_crosses_store_instances(queued):
    from services.storage import ExecutionStore
    store = queued[2]
    independent = ExecutionStore(store.data_dir, store.legacy_log)
    assert independent.cache_revision('aws', '111111111111') == ''
    store.invalidate_cache('aws', '111111111111')
    assert independent.cache_revision('aws', '111111111111')
    assert independent.cache_revision('aws', '222222222222') == ''


def test_schema4_backup_and_interrupted_actions_are_not_adopted(queued):
    import sqlite3
    from services.storage import ExecutionStore
    proposal, _ = approved(queued)
    store = queued[2]
    with store._connection() as db:
        # Recreate the pre-job schema checkpoint without altering the real developer DB.
        entry = store.get(proposal['execution_id'])
        entry.pop('job_id')
        db.execute('UPDATE executions SET payload=? WHERE execution_id=?', (__import__('json').dumps(entry), entry['execution_id']))
        db.execute('DROP TABLE jobs'); db.execute('DROP TABLE job_requests')
        db.execute('PRAGMA user_version=4'); db.commit()
    migrated = ExecutionStore(store.data_dir, store.legacy_log)
    migrated.initialize()
    backup = next((store.data_dir / 'backups').glob('schema-v4-*.sqlite3'))
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
    restored = migrated.get(entry['execution_id'])
    assert restored['status'] == 'uncertain'
    queue = Jobs(migrated)
    assert queue.get(restored['job_id'])['status'] == 'reconciliation_required'
    assert queue.claim('new-worker') is None


def test_mcp_transport_queues_without_dispatch_and_preserves_actor(queued):
    headers = {'Authorization': 'Bearer test-mcp-token', 'Accept': 'application/json, text/event-stream'}
    with TestClient(queued[1].create_app(), headers=headers) as client:
        response = client.post('/mcp/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
        extra = {'mcp-session-id': response.headers['mcp-session-id']} if response.headers.get('mcp-session-id') else {}
        client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        response = client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'run_terraform_plan_mcp', 'arguments': {'hcl_config': 'provider "aws" {}', 'aws_region': 'eu-west-1'}}})
        assert response.status_code == 200
        queued[2].test_runner.run.assert_not_called()
    job = queued[-1].get(queued[-1].list()[0]['job_id'])
    assert job['actor']['kind'] == 'bearer'
    assert job['status'] == 'queued'
    assert job_worker.run_once(queued[-1])
    proposal = queued[-1].get(job['job_id'])['result']
    assert queued[2].action_events(proposal['execution_id'])[0]['actor']['kind'] == 'bearer'


def test_unregistered_operation_cannot_be_queued(queued):
    with pytest.raises(ValueError, match='registered'):
        queued[-1].submit('_approved_action', {}, queued[3].identity)


def test_saved_review_uses_current_action_status_after_execution(queued):
    proposal, job = approved(queued)
    assert job_worker.run_once(queued[-1])
    plan_job = next(j for j in queued[-1].list() if j['operation'] == 'run_terraform_plan_mcp')
    detail = public(queued[-1].get(plan_job['job_id']))
    assert detail['result']['execution_id'] == proposal['execution_id']
    assert detail['result']['status'] == 'complete'


def test_cancel_approved_but_unclaimed_job_revokes_dispatch_atomically(queued):
    proposal, job = approved(queued)
    queued[2].test_runner.run.reset_mock()
    assert queued[-1].cancel(job['job_id'])['status'] == 'cancelled'
    assert queued[2].get(proposal['execution_id'])['status'] == 'rejected'
    assert not job_worker.run_once(queued[-1])
    queued[2].test_runner.run.assert_not_called()
    with queued[2]._connection() as db:
        assert db.execute('SELECT * FROM action_locks').fetchall() == []
