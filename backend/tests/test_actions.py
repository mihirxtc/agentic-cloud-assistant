"""Offline mutation safety: reviewed bytes, atomic claims, races and uncertainty."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

from services import action_service as actions, aws_connection as aws, execution_service as execution
from services.storage import ExecutionStore


@pytest.fixture
def action_env(environment, monkeypatch, tmp_path):
    server, app, store, _ = environment
    session = Mock()
    value = aws.AWSConnection('111111111111', 'eu-west-1', 'arn:aws:iam::111111111111:user/test',
                              'aws', '2026-11-03T12:00:00+00:00', session, 'fake-access', 'fake-secret')
    ec2 = Mock()
    session.client.return_value = ec2
    ec2.describe_security_groups.return_value = {'SecurityGroups': [{'GroupId': 'sg-test', 'IpPermissions': [
        {'IpProtocol': 'tcp', 'FromPort': 20, 'ToPort': 25, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}, {'CidrIp': '10.0.0.0/8'}], 'Ipv6Ranges': [{'CidrIpv6': '::/0'}]},
    ]}]}
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=value))
    monkeypatch.delenv('TF_STATE_BUCKET', raising=False)
    def terraform(command, **kwargs):
        for part in command:
            if part.startswith('-out='):
                (kwargs['cwd'] / part.split('=', 1)[1]).write_bytes(('saved:' + part).encode())
        return SimpleNamespace(returncode=0, stdout='Plan: 1 to add, 0 to change, 0 to destroy.', stderr='')
    run = Mock(side_effect=terraform)
    store.test_runner.run = run
    return server, app, store, value, ec2, run


def plan(env, key=''):
    result = asyncio.run(env[0].run_terraform_plan_mcp('provider "aws" {}', aws_region='eu-west-1', idempotency_key=key))
    assert result['status'] in ('awaiting_approval', 'complete'), (result.get('plan_output'), result.get('error'), result.get('runner_contract'))
    return result


def approve(env, proposal, key='approve-key'):
    return env[0].run_terraform_apply_mcp(proposal['execution_id'], True, approval_digest=proposal['approval_digest'], idempotency_key=key)


def ingress(env, key=''):
    scan = env[2].record_scan(env[3].identity, {'security_groups': {'status': 'ok'}})
    return env[0].revoke_open_ingress_rule('sg-test', 22, scan, idempotency_key=key)


def test_apply_requires_review_digest_and_idempotency_key(action_env):
    env = action_env
    proposal = plan(env)
    env[-1].reset_mock()
    for digest, key in (('', 'new-request'), ('wrong', 'new-request'), (proposal['approval_digest'], '')):
        with pytest.raises(ValueError):
            env[0].run_terraform_apply_mcp(proposal['execution_id'], True, approval_digest=digest, idempotency_key=key)
    env[-1].assert_not_called()
    assert env[2].get(proposal['execution_id'])['status'] == 'awaiting_approval'


@pytest.mark.parametrize('filename', ['tfplan', 'main.tf', 'aca_identity_override.tf.json', '.terraform.lock.hcl'])
def test_changed_or_added_inputs_invalidate_before_dispatch(action_env, filename):
    env = action_env
    proposal = plan(env)
    (env[2].test_runner.root / proposal['workspace_id'] / filename).write_text('changed')
    env[-1].reset_mock()
    result = approve(env, proposal)
    assert result['status'] == 'invalidated'
    env[-1].assert_not_called()


def test_saved_plan_missing_never_reaches_apply(action_env):
    env = action_env
    proposal = plan(env)
    (env[2].test_runner.root / proposal['workspace_id'] / 'tfplan').unlink()
    env[-1].reset_mock()
    assert approve(env, proposal)['status'] == 'invalidated'
    env[-1].assert_not_called()


def test_idempotent_proposal_and_approval_never_repeat_subprocess(action_env):
    env = action_env
    proposal = plan(env, 'plan-once')
    count = env[-1].call_count
    assert plan(env, 'plan-once')['execution_id'] == proposal['execution_id']
    assert env[-1].call_count == count
    with pytest.raises(ValueError, match='different request'):
        asyncio.run(env[0].run_terraform_plan_mcp('provider "aws" {}', description='different', idempotency_key='plan-once'))
    result = approve(env, proposal, 'approve-once')
    assert result['status'] == 'complete'
    count = env[-1].call_count
    assert approve(env, proposal, 'approve-once')['status'] == 'complete'
    assert env[-1].call_count == count
    with pytest.raises(ValueError, match='not awaiting'):
        approve(env, proposal, 'another-key')


def test_concurrent_duplicate_approvals_execute_once(action_env, monkeypatch):
    env = action_env
    proposal = plan(env)
    entered, release = Event(), Event()
    original = env[-1].side_effect
    def blocked(command, **kwargs):
        if command[1] == 'apply':
            entered.set()
            assert release.wait(10)
        return original(command, **kwargs)
    env[-1].side_effect = blocked
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(approve, env, proposal, 'parallel-key')
        assert entered.wait(10)
        second = pool.submit(approve, env, proposal, 'parallel-key').result(timeout=10)
        assert second['status'] == 'applying'
        with pytest.raises(ValueError):
            env[0].run_terraform_apply_mcp(proposal['execution_id'], False, idempotency_key='reject-race')
        release.set()
        assert first.result(timeout=10)['status'] == 'complete'
    assert sum(call.args[0][1] == 'apply' for call in env[-1].call_args_list) == 1


def test_idempotency_key_reuse_for_different_decision_is_rejected(action_env):
    env = action_env
    proposal = plan(env)
    assert approve(env, proposal)['status'] == 'complete'
    with pytest.raises(ValueError, match='different request'):
        env[0].run_terraform_apply_mcp(proposal['execution_id'], False, idempotency_key='approve-key')


def test_rejected_action_cannot_execute_and_legacy_can_only_be_rejected(action_env):
    env = action_env
    proposal = plan(env)
    env[-1].reset_mock()
    env[0].run_terraform_apply_mcp(proposal['execution_id'], False, idempotency_key='reject-once')
    with pytest.raises(ValueError):
        approve(env, proposal)
    env[-1].assert_not_called()
    legacy = 'exec_20261103_120000_1234abcd'
    env[2].append({'execution_id': legacy, 'status': 'awaiting_approval', 'aws_identity': env[3].identity})
    with pytest.raises(ValueError, match='legacy'):
        env[0].run_terraform_apply_mcp(legacy, True, approval_digest='legacy', idempotency_key='legacy-approve')
    assert env[0].run_terraform_apply_mcp(legacy, False)['status'] == 'rejected'


def test_exact_ingress_proposal_does_not_mutate_and_drift_is_rejected(action_env):
    env = action_env
    proposal = ingress(env)
    env[4].revoke_security_group_ingress.assert_not_called()
    assert '10.0.0.0/8' not in proposal['plan_output']
    env[4].describe_security_groups.return_value['SecurityGroups'][0]['IpPermissions'][0]['ToPort'] = 30
    assert approve(env, proposal)['status'] == 'invalidated'
    env[4].revoke_security_group_ingress.assert_not_called()


def test_ingress_approval_sends_only_reviewed_public_sources_once(action_env):
    env = action_env
    proposal = ingress(env)
    assert approve(env, proposal)['status'] == 'complete'
    env[4].revoke_security_group_ingress.assert_called_once_with(GroupId='sg-test', IpPermissions=[{
        'IpProtocol': 'tcp', 'FromPort': 20, 'ToPort': 25,
        'IpRanges': [{'CidrIp': '0.0.0.0/0'}], 'Ipv6Ranges': [{'CidrIpv6': '::/0'}]}])
    approve(env, proposal)
    assert env[4].revoke_security_group_ingress.call_count == 1


def test_competing_ingress_proposals_cannot_both_mutate(action_env, monkeypatch):
    env = action_env
    first, second = ingress(env), ingress(env)
    entered, release = Event(), Event()
    def revoke(**kwargs):
        entered.set()
        assert release.wait(10)
    env[4].revoke_security_group_ingress.side_effect = revoke
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(approve, env, first, 'first-key')
        assert entered.wait(10)
        with pytest.raises(ValueError, match='owns this target'):
            approve(env, second, 'second-key')
        release.set()
        assert pending.result(timeout=10)['status'] == 'complete'
    assert env[4].revoke_security_group_ingress.call_count == 1


def test_destroy_requires_separate_review_and_shared_workspace_lock(action_env):
    env = action_env
    proposal = plan(env)
    with pytest.raises(ValueError):
        asyncio.run(env[0].rollback_execution(proposal['execution_id']))
    approve(env, proposal)
    env[-1].reset_mock()
    destroy = asyncio.run(env[0].rollback_execution(proposal['execution_id'], idempotency_key='destroy-plan'))
    assert destroy['status'] == 'awaiting_approval'
    assert destroy['approval_digest'] != proposal['approval_digest']
    assert destroy['workspace_id'] == proposal['workspace_id']
    assert all(call.args[0][1] != 'apply' for call in env[-1].call_args_list)
    with pytest.raises(ValueError, match='owns this target'):
        asyncio.run(env[0].rollback_execution(proposal['execution_id']))
    assert asyncio.run(env[0].rollback_execution(proposal['execution_id'], idempotency_key='destroy-plan'))['execution_id'] == destroy['execution_id']
    assert approve(env, destroy, 'approve-destroy')['status'] == 'complete'
    assert env[-1].call_args.args[0] == ['terraform', 'apply', '-no-color', destroy['plan_file']]
    assert not any(call.args[0][1] == 'destroy' for call in env[-1].call_args_list)


@pytest.mark.parametrize('failure', ['timeout', 'error', 'nonzero'])
def test_uncertain_or_failed_mutation_is_not_retryable(action_env, failure):
    env = action_env
    proposal = plan(env)
    def failed(command, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        if failure == 'error':
            raise OSError('lost response')
        return SimpleNamespace(returncode=1, stdout='partial mutation', stderr='')
    env[-1].side_effect = failed
    result = approve(env, proposal)
    assert result['status'] == ('failed' if failure == 'nonzero' else 'uncertain')
    calls = env[-1].call_count
    assert approve(env, proposal)['status'] == result['status']
    with pytest.raises(ValueError):
        approve(env, proposal, 'retry-other-key')
    assert env[-1].call_count == calls
    with sqlite3.connect(env[2].path) as database:
        assert database.execute('SELECT execution_id FROM action_locks').fetchone()[0] == proposal['execution_id']


def test_changed_review_metadata_and_generic_updates_are_blocked(action_env):
    env = action_env
    proposal = plan(env)
    with pytest.raises(ValueError, match='transactional'):
        env[2].update(proposal['execution_id'], {'status': 'complete'})
    with sqlite3.connect(env[2].path) as db:
        row = db.execute('SELECT payload FROM executions WHERE execution_id = ?', (proposal['execution_id'],)).fetchone()
        import json
        data = json.loads(row[0]); data['plan_output'] = 'tampered review'
        db.execute('UPDATE executions SET payload = ? WHERE execution_id = ?', (json.dumps(data), proposal['execution_id']))
    env[-1].reset_mock()
    assert approve(env, proposal)['status'] == 'invalidated'
    env[-1].assert_not_called()


def test_transitions_survive_restart_and_http_actor_is_recorded(action_env):
    env = action_env
    with TestClient(env[1].create_app(), headers={'Authorization': 'Bearer test-mcp-token'}) as client:
        response = client.post('/tools/run_terraform_plan_mcp', json={'hcl_config': 'provider "aws" {}', 'aws_region': 'eu-west-1'})
        assert response.status_code == 200
    # REST wrappers return tool content; inspect the persisted proposal directly.
    entry = env[2].history()[-1]
    events = env[2].action_events(entry['execution_id'])
    assert events[0]['actor']['kind'] == 'bearer'
    assert events[-1]['to'] == 'awaiting_approval'
    reopened = ExecutionStore(env[2].data_dir, Path('/nonexistent-history'))
    assert reopened.get(entry['execution_id'])['approval_digest'] == entry['approval_digest']
    assert reopened.action_events(entry['execution_id']) == events


def test_schema_three_upgrade_backs_up_and_preserves_sessions(action_env):
    env = action_env
    env[2].initialize()
    with sqlite3.connect(env[2].path) as db:
        db.execute('PRAGMA user_version = 3')
        db.execute("INSERT INTO sessions VALUES ('token', 'csrf', 'operator', 'config', 12345)")
    reopened = ExecutionStore(env[2].data_dir, Path('/nonexistent-history'))
    reopened.initialize()
    backup = next((env[2].data_dir / 'backups').glob('schema-v3-*.sqlite3'))
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] == 'token'
    with sqlite3.connect(reopened.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] == 'token'


@pytest.mark.parametrize('protocol,port,expected', [('icmp', 22, False), ('udp', 22, False), ('17', 3389, True), ('-1', -1, True)])
def test_ingress_review_and_dispatch_respect_protocols(action_env, protocol, port, expected):
    env = action_env
    env[4].describe_security_groups.return_value['SecurityGroups'][0]['IpPermissions'] = [{
        'IpProtocol': protocol, 'FromPort': port, 'ToPort': port, 'Ipv6Ranges': [{'CidrIpv6': '::/0'}]}]
    scan = env[2].record_scan(env[3].identity, {'security_groups': {'status': 'ok'}})
    proposal = env[0].revoke_open_ingress_rule('sg-test', port, scan)
    assert proposal['status'] == ('awaiting_approval' if expected else 'plan_failed')
    if expected:
        assert approve(env, proposal)['status'] == 'complete'
    assert env[4].revoke_security_group_ingress.call_count == int(expected)


def test_approve_reject_race_has_one_winning_transition(action_env):
    from threading import Barrier
    env = action_env
    proposal = ingress(env)
    ready = Barrier(3)
    def decision(approved):
        ready.wait(timeout=10)
        try:
            return env[0].run_terraform_apply_mcp(proposal['execution_id'], approved,
                    approval_digest=proposal['approval_digest'], idempotency_key='racing-approve' if approved else 'racing-reject')['status']
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        approve_future, reject_future = pool.submit(decision, True), pool.submit(decision, False)
        ready.wait(timeout=10)
        outcomes = [approve_future.result(timeout=10), reject_future.result(timeout=10)]
    assert outcomes.count('conflict') == 1
    assert env[2].get(proposal['execution_id'])['status'] in ('complete', 'rejected')
    assert env[4].revoke_security_group_ingress.call_count <= 1


def test_ingress_lost_response_remains_uncertain_across_restart(action_env):
    env = action_env
    proposal = ingress(env)
    env[4].revoke_security_group_ingress.side_effect = TimeoutError('no response')
    assert approve(env, proposal)['status'] == 'uncertain'
    reopened = ExecutionStore(env[2].data_dir, Path('/nonexistent-history'))
    assert reopened.get(proposal['execution_id'])['status'] == 'uncertain'
    assert approve(env, proposal)['status'] == 'uncertain'
    assert env[4].revoke_security_group_ingress.call_count == 1


def test_crash_claim_is_not_replayed_after_restart(action_env):
    env = action_env
    proposal = ingress(env)
    key = 'decision:interrupted-request'
    digest = actions.fingerprint({'execution_id': proposal['execution_id'], 'approved': True, 'digest': proposal['approval_digest']})
    env[2].claim_action(proposal['execution_id'], True, proposal['approval_digest'], key, digest)
    reopened = ExecutionStore(env[2].data_dir, Path('/nonexistent-history'))
    entry, should_run = reopened.claim_action(proposal['execution_id'], True, proposal['approval_digest'], key, digest)
    assert entry['status'] == 'applying'
    assert should_run is False
    env[4].revoke_security_group_ingress.assert_not_called()


def test_mounted_mcp_transport_preserves_http_audit_identity(action_env):
    env = action_env
    headers = {'Authorization': 'Bearer test-mcp-token', 'Accept': 'application/json, text/event-stream'}
    with TestClient(env[1].create_app(), headers=headers) as client:
        response = client.post('/mcp/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
        assert response.status_code == 200
        sid = response.headers.get('mcp-session-id')
        extra = {'mcp-session-id': sid} if sid else {}
        client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        response = client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'run_terraform_plan_mcp', 'arguments': {'hcl_config': 'provider "aws" {}', 'aws_region': 'eu-west-1'}}})
        assert response.status_code == 200
    entry = env[2].history()[-1]
    assert env[2].action_events(entry['execution_id'])[0]['actor']['kind'] == 'bearer'
