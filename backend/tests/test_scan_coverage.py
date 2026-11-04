"""Permission failures and incomplete inventories must never become clean scans."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from botocore.exceptions import ClientError

from services import aws_scanner as scanner
from services.scan_coverage import assessment, scan_coverage, SERVICE_ITEMS
from services.security_analyzer import run_security_analysis


def denied():
    return ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'sensitive SDK details'}}, 'Inspect')


@pytest.fixture
def clients(monkeypatch):
    clients = {name: Mock() for name in ('ec2', 'iam', 's3', 's3control')}
    connection = SimpleNamespace(account_id='111111111111', client=lambda name, **kwargs: clients[name])
    monkeypatch.setattr(scanner, 'get_connection', lambda *args: connection)
    return clients


def paginate(client, operations):
    def paginator(operation):
        def pages(**kwargs):
            result = operations[operation]
            if callable(result):
                return result(**kwargs)
            return iter(result)
        return SimpleNamespace(paginate=pages)
    client.get_paginator.side_effect = paginator


def complete_scan():
    return {name: {'status': 'ok', 'coverage': 'complete', key: []} for name, key in SERVICE_ITEMS.items()}


def test_failed_service_retains_confirmed_findings_and_provenance():
    scan = complete_scan()
    scan.update(scan_id='scan-test', aws_identity={'account_id': '111111111111'})
    scan['s3'] = {'status': 'error', 'error': 'Denied', 'buckets': []}
    scan['security_groups']['security_groups'] = [{'id': 'sg-test', 'open_to_internet': [
        {'port': 20, 'to_port': 25, 'protocol': '6', 'sources': ['::/0']}]}]
    result = assessment(scan)
    assert result['status'] == 'partial'
    assert result['total_findings'] == 1
    assert result['findings'][0]['scan_id'] == 'scan-test'
    assert result['findings'][0]['metadata']['sources'] == ['::/0']
    assert 'Uninspected' in result['llm_summary']


def test_missing_and_malformed_services_are_failed_not_clean():
    assert assessment({})['status'] == 'failed'
    assert assessment({name: {'status': 'ok'} for name in SERVICE_ITEMS})['status'] == 'failed'
    assert assessment({name: None for name in SERVICE_ITEMS})['status'] == 'failed'
    assert assessment(complete_scan())['status'] == 'complete'
    assert 'secure' in assessment(complete_scan())['llm_summary']


def test_unknown_mfa_is_visible_and_does_not_create_a_finding():
    scan = complete_scan()
    scan['iam']['users'] = [{'username': 'alice', 'has_mfa': None, 'last_login': None}]
    result = assessment(scan)
    assert result['status'] == 'partial'
    assert result['coverage']['rules']['IAM_USER_NO_MFA']['unknown_resources'] == ['alice']
    assert result['findings'] == []


def test_paginated_inventory_failure_retains_observed_resources(clients):
    def interrupted(**kwargs):
        yield {'Reservations': [{'Instances': [{'InstanceId': 'i-observed'}]}]}
        raise denied()
    paginate(clients['ec2'], {'describe_instances': interrupted})
    result = scanner.scan_ec2('eu-west-1')
    assert result['coverage'] == 'partial'
    assert result['inventory_complete'] is False
    assert result['instances'][0]['id'] == 'i-observed'
    assert result['started_at'] <= result['completed_at']
    assert 'sensitive' not in str(result)


def test_vpc_subnet_failure_does_not_become_zero(clients):
    def fail(**kwargs):
        raise denied()
    paginate(clients['ec2'], {'describe_vpcs': [{'Vpcs': [{'VpcId': 'vpc-test', 'IsDefault': True}]}], 'describe_subnets': fail})
    result = scanner.scan_vpc()
    assert result['coverage'] == 'partial'
    assert result['vpcs'][0]['subnet_count'] is None
    assert run_security_analysis({'vpc': result})[0]['rule'] == 'DEFAULT_VPC_IN_USE'


def test_iam_detail_denial_and_pagination(clients):
    def fail(**kwargs):
        raise denied()
    paginate(clients['iam'], {
        'list_users': [{'Users': [{'UserName': 'alice', 'CreateDate': '2020-01-01'}]}],
        'list_mfa_devices': fail,
        'list_attached_user_policies': [{'AttachedPolicies': [{'PolicyName': 'first', 'PolicyArn': 'arn:first'}]},
                                        {'AttachedPolicies': [{'PolicyName': 'second', 'PolicyArn': 'arn:second'}]}],
        'list_user_policies': [{'PolicyNames': []}], 'list_groups_for_user': [{'Groups': []}],
        'list_access_keys': [{'AccessKeyMetadata': [{'AccessKeyId': 'test-key', 'Status': 'Active'}]}],
    })
    clients['iam'].get_access_key_last_used.side_effect = denied()
    result = scanner.scan_iam()
    user = result['users'][0]
    assert result['coverage'] == 'partial'
    assert user['has_mfa'] is None
    assert len(user['attached_policies']) == 2
    assert user['access_keys'][0]['last_used'] is None
    assert user['field_statuses']['list_mfa_devices'] == 'unknown'
    assert not any(f['rule'] == 'IAM_USER_NO_MFA' for f in run_security_analysis({'iam': result}))


@pytest.mark.parametrize('account_flags,bucket_flags,policy,acl,expected', [
    ({}, {}, True, False, True),
    ({'RestrictPublicBuckets': True}, {}, True, False, False),
    ({}, {'BlockPublicPolicy': True}, True, False, True),  # blocks NEW policies only
    ({'IgnorePublicAcls': True}, {}, False, True, False),
    (None, {}, False, True, None),
    ({}, None, True, False, None),
    ({}, {}, False, False, False),
])
def test_s3_policy_acl_and_effective_blocks(clients, account_flags, bucket_flags, policy, acl, expected):
    paginate(clients['s3'], {'list_buckets': [{'Buckets': [{'Name': 'bucket-one'}]}, {'Buckets': [{'Name': 'bucket-two'}]}]})
    for client, flags in ((clients['s3control'], account_flags), (clients['s3'], bucket_flags)):
        if flags is None:
            client.get_public_access_block.side_effect = denied()
        else:
            client.get_public_access_block.return_value = {'PublicAccessBlockConfiguration': flags}
    clients['s3'].get_bucket_acl.return_value = {'Grants': [{'Grantee': {'URI': 'http://acs.amazonaws.com/groups/global/AuthenticatedUsers'}}] if acl else []}
    clients['s3'].get_bucket_policy_status.return_value = {'PolicyStatus': {'IsPublic': policy}}
    result = scanner.scan_s3()
    assert result['count'] == 2
    assert result['buckets'][0]['is_public'] is expected
    assert result['coverage'] == ('partial' if account_flags is None or bucket_flags is None else 'complete')


def test_s3_denied_acl_is_unknown_not_private(clients):
    paginate(clients['s3'], {'list_buckets': [{'Buckets': [{'Name': 'bucket'}]}]})
    for client in (clients['s3'], clients['s3control']):
        client.get_public_access_block.return_value = {'PublicAccessBlockConfiguration': {}}
    clients['s3'].get_bucket_acl.side_effect = denied()
    clients['s3'].get_bucket_policy_status.return_value = {'PolicyStatus': {'IsPublic': False}}
    result = scanner.scan_s3()
    assert result['buckets'][0]['is_public'] is None
    assert result['coverage'] == 'partial'


def test_security_groups_keep_ranges_families_and_nonpublic_sources(clients):
    paginate(clients['ec2'], {'describe_security_groups': [{'SecurityGroups': [{'GroupId': 'sg-test', 'IpPermissions': [
        {'IpProtocol': 'tcp', 'FromPort': 20, 'ToPort': 30, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}], 'Ipv6Ranges': [{'CidrIpv6': '::/0'}]},
        {'IpProtocol': 'tcp', 'FromPort': 20, 'ToPort': 40, 'Ipv6Ranges': [{'CidrIpv6': '::/0'}]},
        {'IpProtocol': 'icmp', 'FromPort': 22, 'ToPort': 22, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
        {'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': '10.0.0.0/8'}], 'UserIdGroupPairs': [{'GroupId': 'sg-source'}]},
    ]}]}]})
    result = scanner.scan_security_groups()
    group = result['security_groups'][0]
    assert len(group['ingress_rules']) == 4
    assert len(group['open_to_internet']) == 3
    assert group['open_to_internet'][0]['sources'] == ['0.0.0.0/0', '::/0']
    assert group['open_to_internet'][1]['to_port'] == 40
    assert group['ingress_rules'][3]['source_security_groups'] == [{'GroupId': 'sg-source'}]
    assert len(run_security_analysis({'security_groups': result})) == 1


def test_icmp_type_22_is_not_a_tcp_ssh_port():
    from services.security_analyzer import port_exposed
    assert port_exposed({'protocol': 'icmp', 'port': 22, 'to_port': 22}, 22) is False


def test_partial_assessment_is_consistent_and_agent_stops_before_model(environment, monkeypatch):
    server, _, _, _ = environment
    from services import aws_connection
    connection = Mock()
    connection.identity = {'account_id': '111111111111'}
    monkeypatch.setattr(aws_connection, 'verify_connection', Mock(return_value=connection))
    scan = complete_scan()
    scan['iam'] = {'status': 'error', 'users': []}
    monkeypatch.setattr(server, '_collect_scan', lambda region: scan)
    model = AsyncMock()
    monkeypatch.setattr(server, 'prompt_llm', model)
    tool = asyncio.run(server.run_security_analysis_with_summary(region='eu-west-1'))
    resource = server.aws_findings_resource('eu-west-1')
    agent = asyncio.run(server.agent_run(region='eu-west-1'))
    assert tool['status'] == resource['status'] == agent['status'] == 'partial'
    assert agent['findings'] == tool['findings']
    assert 'Remediation was not started' in agent['message']
    model.assert_not_called()


def test_malformed_rules_cannot_hide_valid_peer_findings():
    scan = complete_scan()
    scan['security_groups']['security_groups'] = [{'id': 'sg-test', 'open_to_internet': [
        None, {'protocol': 'tcp', 'port': 22, 'to_port': 22}]}]
    result = assessment(scan)
    assert result['status'] == 'partial'
    assert result['total_findings'] == 1
    scan['s3']['coverage'] = 'complete'
    scan['s3']['status'] = 'error'
    assert scan_coverage(scan)['services']['s3']['status'] == 'failed'


def test_failed_eni_scan_does_not_declare_groups_unused(clients):
    def fail(**kwargs):
        raise denied()
    paginate(clients['ec2'], {'describe_security_groups': [{'SecurityGroups': [{'GroupId': 'sg-test'}]}],
                              'describe_network_interfaces': fail})
    result = scanner.scan_sg_usage()
    assert result['coverage'] == 'partial'
    assert result['unused_count'] is None
    assert result['unused_sg_ids'] is None


def test_recent_never_used_console_is_not_90_day_inactivity():
    from datetime import datetime, timezone
    scan = complete_scan()
    scan['iam']['users'] = [{'username': 'new-user', 'has_mfa': True, 'last_login': 'Never',
                              'created': datetime.now(timezone.utc).isoformat()}]
    assert assessment(scan)['findings'] == []


def test_model_summary_failure_preserves_findings(environment, monkeypatch):
    server, _, _, _ = environment
    from services import aws_connection
    connection = Mock()
    connection.identity = {'account_id': '111111111111'}
    monkeypatch.setattr(aws_connection, 'verify_connection', Mock(return_value=connection))
    scan = complete_scan()
    scan['s3']['buckets'] = [{'name': 'bucket', 'is_public': True}]
    monkeypatch.setattr(server, '_collect_scan', lambda region: scan)
    monkeypatch.setattr(server, 'prompt_llm', AsyncMock(side_effect=RuntimeError('private model details')))
    result = asyncio.run(server.run_security_analysis_with_summary(region='eu-west-1'))
    assert result['status'] == 'complete'
    assert result['total_findings'] == 1
    assert result['summary_error']
    assert 'private model' not in str(result)


@pytest.mark.parametrize('protocol,port,expected', [('udp', 22, False), ('icmp', 22, False), ('17', 3389, True), ('tcp', 3389, True), ('-1', 22, True)])
def test_protocol_specific_ports(protocol, port, expected):
    from services.security_analyzer import port_exposed
    assert port_exposed({'protocol': protocol, 'port': port, 'to_port': port}, port) is expected
