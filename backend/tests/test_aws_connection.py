"""AWS account binding using frozen test credentials; never contact AWS."""
import asyncio
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from services import aws_connection as aws
from services.aws_connection import AWSConnection, AWSIdentityError

ACCOUNT = '111111111111'
OTHER_ACCOUNT = '222222222222'
EXECUTION_ID = 'exec_20261028_120000_1234abcd'


def connection(account=ACCOUNT, region='eu-west-1', token='test-session-token'):
    session = Mock()
    return AWSConnection(account, region, f'arn:aws:iam::{account}:user/test', 'aws',
                         '2026-10-28T12:00:00+00:00', session, 'test-access-key', 'test-secret-key', token)


def fake_terraform(*args, **kwargs):
    for part in args[0]:
        if part.startswith('-out='):
            (kwargs['cwd'] / part.split('=', 1)[1]).write_bytes(b'test saved plan')
    return SimpleNamespace(returncode=0, stdout='Plan: 1 to add, 0 to change, 0 to destroy.', stderr='')


@pytest.fixture
def verified(environment, monkeypatch):
    server, _, store, _ = environment
    value = connection()
    clients = {name: Mock() for name in ('ec2', 's3', 's3control', 'iam', 'ce')}
    value.session.client.side_effect = lambda service, **kwargs: clients[service]
    for name in ('ec2', 's3', 'iam'):
        clients[name].get_paginator.return_value.paginate.return_value = []
    clients['s3control'].get_public_access_block.return_value = {'PublicAccessBlockConfiguration': {}}
    clients['ce'].get_cost_and_usage.return_value = {'ResultsByTime': []}
    clients['ec2'].describe_security_groups.return_value = {'SecurityGroups': [{
        'GroupId': 'sg-test', 'IpPermissions': [{'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22,
                                             'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}],
    }]}
    verifier = Mock(return_value=value)
    monkeypatch.setattr(aws, 'verify_connection', verifier)
    import services.aws_cache as cache
    cache._cache.clear()
    return server, store, value, clients, verifier


def test_sts_uses_the_same_frozen_credentials_as_subsequent_clients(monkeypatch):
    source, frozen_session = Mock(), Mock()
    source.get_credentials.return_value.get_frozen_credentials.return_value = SimpleNamespace(
        access_key='test-access', secret_key='test-secret', token='test-token')
    frozen_session.client.return_value.get_caller_identity.return_value = {
        'Account': ACCOUNT, 'Arn': f'arn:aws:iam::{ACCOUNT}:user/test'}
    frozen_session.get_partition_for_region.return_value = 'aws'
    factory = Mock(side_effect=[source, frozen_session])
    monkeypatch.setattr(aws.boto3, 'Session', factory)
    monkeypatch.delenv('ACA_AWS_ACCOUNT_ID', raising=False)
    result = aws.verify_connection('eu-west-1')
    assert factory.call_args.kwargs == {'aws_access_key_id': 'test-access', 'aws_secret_access_key': 'test-secret',
                                       'aws_session_token': 'test-token', 'region_name': 'eu-west-1'}
    assert result.session is frozen_session
    assert result.identity['account_id'] == ACCOUNT
    assert all(secret not in repr(result) + json.dumps(result.identity) for secret in ('test-access', 'test-secret', 'test-token'))


@pytest.mark.parametrize('failure', ['missing', 'sts', 'invalid', 'pin'])
def test_unverified_or_wrong_server_credentials_fail_closed(monkeypatch, failure):
    source, frozen_session = Mock(), Mock()
    source.get_credentials.return_value.get_frozen_credentials.return_value = SimpleNamespace(
        access_key='test-access', secret_key='test-secret', token=None)
    frozen_session.client.return_value.get_caller_identity.return_value = {
        'Account': ACCOUNT, 'Arn': f'arn:aws:iam::{ACCOUNT}:user/test'}
    frozen_session.get_partition_for_region.return_value = 'aws'
    if failure == 'missing':
        source.get_credentials.return_value = None
    elif failure == 'sts':
        frozen_session.client.return_value.get_caller_identity.side_effect = RuntimeError('test-secret should not leak')
    elif failure == 'invalid':
        frozen_session.client.return_value.get_caller_identity.return_value['Account'] = 'not-an-account'
    monkeypatch.setattr(aws.boto3, 'Session', Mock(side_effect=[source, frozen_session]))
    monkeypatch.setenv('ACA_AWS_ACCOUNT_ID', OTHER_ACCOUNT if failure == 'pin' else '')
    with pytest.raises(AWSIdentityError) as error:
        aws.verify_connection('eu-west-1')
    assert 'test-secret' not in str(error.value)


def test_default_region_is_resolved_from_server_configuration(monkeypatch):
    monkeypatch.setenv('AWS_REGION', 'eu-west-1')
    source = Mock()
    source.get_credentials.return_value = None
    factory = Mock(return_value=source)
    monkeypatch.setattr(aws.boto3, 'Session', factory)
    with pytest.raises(AWSIdentityError):
        aws.verify_connection()
    factory.assert_called_once_with(region_name='eu-west-1')


def test_scan_verifies_once_and_persists_provenance_without_credentials(verified):
    server, store, value, _, verifier = verified
    result = server.full_aws_scan(region=value.region, expected_account_id=ACCOUNT)
    verifier.assert_called_once_with(value.region)
    assert result['aws_identity'] == value.identity
    record = store.get_scan(result['scan_id'])
    assert record['aws_identity'] == value.identity
    assert all(status == 'ok' for status in record['service_statuses'].values())
    assert 'test-secret-key' not in json.dumps(record)
    assert server.analyse_security_findings(result)['total_findings'] == 0
    assert server.analyse_security_findings(result)['status'] == 'complete'


def test_expected_browser_account_blocks_before_any_service_call(verified, monkeypatch):
    server, _, value, _, _ = verified
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=connection(OTHER_ACCOUNT)))
    with pytest.raises(AWSIdentityError, match='changed'):
        server.full_aws_scan(region=value.region, expected_account_id=ACCOUNT)
    value.session.client.assert_not_called()


def test_cost_and_inventory_use_the_same_operation_credentials(verified):
    server, _, value, clients, verifier = verified
    result = server.estimate_costs(region=value.region)
    assert result['aws_identity']['account_id'] == ACCOUNT
    assert all(call.kwargs['region_name'] == 'us-east-1' for call in value.session.client.call_args_list)
    assert clients['ce'].get_cost_and_usage.call_count == 3
    verifier.assert_called_once()


@pytest.mark.parametrize('action', ['apply', 'agent', 'destroy'])
@pytest.mark.parametrize('mismatch', ['account', 'region', 'legacy'])
def test_execution_identity_mismatch_never_runs_mutation(verified, monkeypatch, action, mismatch):
    server, store, value, clients, _ = verified
    identity = value.identity if mismatch != 'legacy' else None
    store.append({'execution_id': EXECUTION_ID, 'status': 'awaiting_approval', 'aws_identity': identity,
                  'action_type': 'sg_revoke', 'sg_id': 'sg-test', 'port': 22})
    if mismatch == 'account':
        monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=connection(OTHER_ACCOUNT)))
    region = 'us-east-1' if mismatch == 'region' else value.region
    if mismatch == 'region':
        monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=replace(value, region=region)))
    with pytest.raises(AWSIdentityError):
        if action == 'apply':
            server.run_terraform_apply_mcp(EXECUTION_ID, True, aws_region=region)
        elif action == 'agent':
            server.agent_approve(EXECUTION_ID, True, aws_region=region)
        else:
            asyncio.run(server.rollback_execution(EXECUTION_ID, aws_region=region))
    clients['ec2'].revoke_security_group_ingress.assert_not_called()
    assert store.get(EXECUTION_ID)['status'] == 'awaiting_approval'


def test_legacy_proposal_can_be_rejected_without_aws(verified):
    server, store, _, _, verifier = verified
    store.append({'execution_id': EXECUTION_ID, 'status': 'awaiting_approval'})
    assert server.run_terraform_apply_mcp(EXECUTION_ID, False)['status'] == 'rejected'
    verifier.assert_not_called()


def test_direct_fix_requires_persisted_scan_identity(verified, monkeypatch):
    server, store, value, clients, _ = verified
    scan_id = store.record_scan(value.identity, {'security_groups': {'status': 'ok'}})
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=connection(OTHER_ACCOUNT)))
    with pytest.raises(AWSIdentityError):
        server.revoke_open_ingress_rule('sg-test', 22, scan_id, region=value.region)
    clients['ec2'].revoke_security_group_ingress.assert_not_called()
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=value))
    result = server.revoke_open_ingress_rule('sg-test', 22, scan_id, region=value.region)
    assert result['status'] == 'awaiting_approval'
    clients['ec2'].revoke_security_group_ingress.assert_not_called()
    approved = server.agent_approve(result['execution_id'], True, approval_digest=result['approval_digest'], idempotency_key='test-approve')
    assert approved['status'] == 'complete'
    clients['ec2'].revoke_security_group_ingress.assert_called_once()


def test_terraform_receives_frozen_tokens_and_persists_account_guard(verified, environment, tmp_path, monkeypatch):
    server, store, value, _, _ = verified
    import services.execution_service as execution
    monkeypatch.delenv('TF_STATE_BUCKET', raising=False)
    monkeypatch.setenv('AWS_PROFILE', 'must-not-be-reused')
    monkeypatch.setenv('AWS_SESSION_TOKEN', 'stale-token')
    run = Mock(side_effect=fake_terraform)
    store.test_runner.run = run
    result = asyncio.run(server.run_terraform_plan_mcp('provider "aws" {}', aws_region=value.region))
    assert result['status'] == 'awaiting_approval'
    assert store.get(result['execution_id'])['aws_identity'] == value.identity
    env = run.call_args.kwargs['env']
    assert env['AWS_SESSION_TOKEN'] == value.token
    assert env['AWS_REGION'] == env['AWS_DEFAULT_REGION'] == env['TF_VAR_region'] == value.region
    assert 'AWS_PROFILE' not in env
    assert 'ACA_OPERATOR_PASSWORD_HASH' not in env
    assert env['AWS_SECRET_ACCESS_KEY'] == value.secret_key
    guard = json.loads((store.test_runner.root / result['execution_id'] / 'aca_identity_override.tf.json').read_text())
    assert guard['provider']['aws'][0]['allowed_account_ids'] == [ACCOUNT]
    assert 'test-secret-key' not in json.dumps(result) + json.dumps(store.history())


def test_missing_or_changed_terraform_guard_blocks_apply(verified, tmp_path, monkeypatch):
    _, store, value, _, _ = verified
    import services.execution_service as execution
    monkeypatch.setattr(execution, 'get_connection', lambda: value)
    monkeypatch.setattr(aws, 'verify_connection', Mock(return_value=value))
    store.append({'execution_id': EXECUTION_ID, 'aws_identity': value.identity})
    result = execution.run_terraform_apply(EXECUTION_ID)
    assert not result['success']
    assert result.get('uncertain') is False  # unclaimed internal apply is blocked before dispatch


def test_long_lived_credentials_clear_stale_session_tokens(verified, tmp_path, monkeypatch):
    _, _, value, _, _ = verified
    from services import execution_service as execution
    monkeypatch.setenv('AWS_SESSION_TOKEN', 'stale')
    monkeypatch.setenv('AWS_SECURITY_TOKEN', 'stale')
    env = execution._build_env(replace(value, token=None))
    assert 'AWS_SESSION_TOKEN' not in env
    assert 'AWS_SECURITY_TOKEN' not in env


def test_cache_separates_accounts_regions_principals_and_invalidates_account(verified):
    _, _, value, _, _ = verified
    from services.aws_cache import cached_context, invalidate_account
    build = Mock(side_effect=lambda: object())
    initial = cached_context(value, build)
    assert cached_context(value, build) is initial
    regional = replace(value, region='us-east-1')
    other = connection(OTHER_ACCOUNT)
    assert cached_context(regional, build) is not initial
    other_result = cached_context(other, build)
    assert cached_context(replace(value, arn=value.arn + '-other'), build) is not initial
    invalidate_account(value)
    assert cached_context(value, build) is not initial
    assert cached_context(other, build) is other_result


def test_partial_mutation_failure_invalidates_cached_reads(verified):
    _, _, value, _, _ = verified
    from services.aws_cache import cached_context
    before = cached_context(value, object)

    @aws.aws_operation(mutating=True)
    def partial_failure(region=''):
        raise RuntimeError('simulated failure after a write')

    with pytest.raises(RuntimeError):
        partial_failure(value.region)
    assert cached_context(value, object) is not before


def test_authenticated_identity_endpoint_and_removed_credentials_schema(verified, environment, monkeypatch):
    _, factory, _, _ = environment
    _, _, value, _, _ = verified
    monkeypatch.setattr(factory, 'verify_connection', lambda region: value)
    with TestClient(factory.create_app(), headers={'Authorization': 'Bearer test-mcp-token'}) as client:
        response = client.get('/auth/aws-identity', params={'region': value.region})
        assert response.json()['aws_identity'] == value.identity
        assert 'test-secret-key' not in response.text
        assert client.post('/tools/full_aws_scan', json={'region': value.region, 'aws_secret_access_key': 'old-override'}).status_code == 422
        schema = client.get('/openapi.json').json()
        assert 'aws_secret_access_key' not in json.dumps(schema)


@pytest.mark.parametrize('hcl', [
    'provider "aws" { access_key = "override" }',
    'provider "aws" { profile = "other-account" }',
    'provider "aws" { alias = "other" }',
    'provider "aws" { assume_role { role_arn = "arn:aws:iam::222222222222:role/test" } }',
    'provider "aws" { region = "us-east-1" }',
    'provider "aws" { skip_requesting_account_id = true }',
    'provider "aws" { endpoints { sts = "http://untrusted.test" } }',
    'module "nested" { source = "./nested" }',
    'terraform { backend "s3" { bucket = "other" } }',
    'terraform { required_providers { aws = { source = "untrusted/aws" } } }',
    'resource "aws_instance" "x" { provider = aws.other }',
    'resource "aws_instance" "x" { region = "us-east-1" }',
    'resource "aws_instance" "x" { provisioner "local-exec" { command = "echo test" } }',
])
def test_terraform_account_bypasses_are_rejected_before_init(verified, monkeypatch, hcl):
    _, store, value, _, _ = verified
    import services.execution_service as execution
    monkeypatch.setattr(execution, 'get_connection', lambda: value)
    run = Mock(side_effect=AssertionError('Terraform must not start'))
    store.test_runner.run = run
    result = execution.run_terraform_plan(hcl, EXECUTION_ID)
    assert result['success'] is False
    run.assert_not_called()


def test_normal_generated_root_configuration_is_supported(verified):
    _, _, value, _, _ = verified
    from services.aws_terraform_guard import validate_aws_configuration
    hcl = '''
    terraform {
      required_providers {
        aws = { source = "hashicorp/aws", version = "~> 5.0" }
        random = { source = "hashicorp/random" }
        tls = { source = "hashicorp/tls" }
        local = { source = "hashicorp/local" }
      }
    }
    variable "region" { default = "us-east-1" }
    provider "aws" { region = var.region }
    resource "random_id" "suffix" { byte_length = 4 }
    resource "aws_instance" "instance" { ami = "ami-test" instance_type = "t3.micro" }
    '''
    assert validate_aws_configuration(hcl, value)['provider'][0]['aws']['region'] == '${var.region}'


def test_successful_apply_and_destroy_keep_recorded_account(verified, tmp_path, monkeypatch):
    server, store, value, _, _ = verified
    import services.execution_service as execution
    monkeypatch.delenv('TF_STATE_BUCKET', raising=False)
    run = Mock(side_effect=fake_terraform)
    store.test_runner.run = run
    planned = asyncio.run(server.run_terraform_plan_mcp('provider "aws" {}', aws_region=value.region))
    execution_id = planned['execution_id']
    run.reset_mock()
    assert server.run_terraform_apply_mcp(execution_id, True, approval_digest=planned['approval_digest'], idempotency_key='apply-test')['status'] == 'complete'
    assert run.call_args.args[0][1] == 'apply'
    assert run.call_args.kwargs['env']['AWS_SESSION_TOKEN'] == value.token
    assert store.get(execution_id)['aws_identity']['region'] == value.region
    run.reset_mock()
    destroy = asyncio.run(server.rollback_execution(execution_id))
    assert destroy['status'] == 'awaiting_approval'
    assert run.call_args.args[0][1] == 'plan'
    assert '-destroy' in run.call_args.args[0]
    approved = server.run_terraform_apply_mcp(destroy['execution_id'], True, approval_digest=destroy['approval_digest'], idempotency_key='destroy-test')
    assert approved['status'] == 'complete'
    assert run.call_args.args[0][1] == 'apply'
    assert run.call_args.args[0][-1] == destroy['plan_file']


def test_identity_endpoint_failure_does_not_reveal_sdk_details(environment, monkeypatch):
    _, factory, _, _ = environment
    def unavailable(region):
        raise AWSIdentityError('Unable to verify AWS identity')
    monkeypatch.setattr(factory, 'verify_connection', unavailable)
    with TestClient(factory.create_app(), headers={'Authorization': 'Bearer test-mcp-token'}) as client:
        assert client.get('/auth/aws-identity').status_code == 503


def test_failed_scan_cannot_authorize_a_direct_fix(verified):
    server, store, value, clients, verifier = verified
    scan_id = store.record_scan(value.identity, {'security_groups': {'status': 'error'}})
    with pytest.raises(AWSIdentityError, match='successful'):
        server.revoke_open_ingress_rule('sg-test', 22, scan_id)
    verifier.assert_not_called()
    clients['ec2'].revoke_security_group_ingress.assert_not_called()


def test_async_workflow_propagates_connection_to_worker_and_clears_context(verified):
    _, _, value, _, verifier = verified

    @aws.aws_operation()
    async def workflow(region=''):
        from_worker = await asyncio.to_thread(aws.get_connection)
        assert from_worker is value
        return {'ok': True}

    assert asyncio.run(workflow(value.region))['aws_identity'] == value.identity
    verifier.assert_called_once()
    assert aws._active_connection.get() is None


def test_v2_database_upgrade_preserves_sessions_and_adds_scan_records(verified, tmp_path):
    from services.storage import ExecutionStore
    store = ExecutionStore(tmp_path / 'old-data', tmp_path / 'missing.json')
    store.initialize()
    store.create_session('test-hash', 'test-csrf', 'operator', 'tag', 9999999999, 0)
    with sqlite3.connect(store.path) as database:
        database.execute('DROP TABLE scans')
        database.execute('PRAGMA user_version = 2')
    reopened = ExecutionStore(store.data_dir, store.legacy_log)
    reopened.initialize()
    assert reopened.session('test-hash', 'tag', 1)['operator'] == 'operator'
    assert list((store.data_dir / 'backups').glob('schema-v2-*.sqlite3'))
    scan_id = reopened.record_scan(connection().identity, {'ec2': {'status': 'ok'}})
    assert reopened.get_scan(scan_id)['aws_identity']['account_id'] == ACCOUNT
