"""Runner security boundaries tested without Docker, AWS or external networking."""
import base64
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest

from runner import broker as broker_module, job, paths, protocol
from runner.broker import Broker, make_handler
from runner.policy import validate_source, validate_plan, server_override, VERSIONS
from services import runner_client

IDENTIFIER = 'exec_20261103_120000_1234abcd'
IDENTITY = {'account_id': '111111111111', 'region': 'eu-west-1', 'partition': 'aws', 'arn': 'arn:aws:iam::111111111111:user/test'}
IMAGE = 'sha256:' + 'a' * 64
CREDENTIALS = {'access_key': 'fake-access', 'secret_key': 'fake-secret', 'token': 'fake-session'}
TOKEN = 'b' * 64


def request(operation='plan', **extra):
    value = {'operation': operation, 'workspace_id': IDENTIFIER, 'identity': IDENTITY, **extra}
    if operation in ('plan', 'apply', 'destroy_plan'):
        value['credentials'] = CREDENTIALS.copy()
    if operation == 'plan':
        value.update(hcl='provider "aws" {}', state_backend=None)
    if operation in ('apply', 'manifest', 'destroy_plan'):
        value.setdefault('plan_file', 'tfplan')
    return value


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    broker = Broker(tmp_path / 'workspaces', tmp_path / 'control', IMAGE, 'test_workspaces', 'test_jobs', {})
    def fake_job(command, stdin):
        body = json.loads(stdin)
        root = paths.workspace(broker.root, body['workspace_id'])
        if body['operation'] != 'apply':
            if not (root / '.terraform.lock.hcl').exists():
                paths.write_file(root, '.terraform.lock.hcl', b'canonical dependency lock')
            paths.write_file(root, body.get('plan_file', 'tfplan'), b'opaque saved plan')
            return {'success': True, 'plan_output': 'No changes', 'resources_to_add': 0}
        return {'success': True, 'apply_output': 'Complete', 'owned': {}, 'key_files': []}
    launch = Mock(side_effect=fake_job)
    monkeypatch.setattr(broker_module, 'run_container', launch)
    def inspect(command, **kwargs):
        if command[1] == 'version':
            return Mock(returncode=0, stdout=b'28.0.0')
        value = [{'Id': IMAGE, 'Config': {'User': '10001:10001', 'Entrypoint': ['python','-m','runner.job']}}] if command[1:3] == ['image','inspect'] else [{'Internal': True}]
        return Mock(returncode=0, stdout=json.dumps(value).encode())
    engine = Mock(side_effect=inspect)
    monkeypatch.setattr(broker_module.subprocess, 'run', engine)
    return broker, launch, engine


def test_broker_only_mounts_selected_workspace_and_never_persists_credentials(broker_env):
    broker, launch, _ = broker_env
    result = broker.handle(request())
    assert result['success']
    command, stdin = launch.call_args.args
    assert '--user' in command and '10001:10001' in command
    assert '--read-only' in command and '--cap-drop=ALL' in command
    assert '--security-opt=no-new-privileges' in command
    assert '--pids-limit=64' in command and '--memory=768m' in command and '--cpus=1' in command
    assert f'type=volume,source=test_workspaces,target=/workspace,volume-subpath={IDENTIFIER}' in command
    assert not any('docker.sock' in arg or '/control' in arg or '/app' in arg for arg in command)
    assert command[-1] == IMAGE
    assert CREDENTIALS['secret_key'] not in str(command)
    assert json.loads(stdin)['credentials'] == CREDENTIALS
    persisted = b''.join(path.read_bytes() for folder in (broker.root, broker.control) for path in folder.rglob('*') if path.is_file())
    assert all(value.encode() not in persisted for value in CREDENTIALS.values())


@pytest.mark.parametrize('extra', [{'command': 'sh'}, {'image': 'other'}, {'mounts': ['/']}, {'policy': {}}, {'owned': {}}, {'environment': {'TF_CLI_ARGS': '-target=anything'}}])
def test_caller_cannot_select_engine_privileges_commands_or_policy(broker_env, extra):
    broker, launch, _ = broker_env
    with pytest.raises(ValueError):
        broker.handle(request(**extra))
    launch.assert_not_called()


def test_broker_checks_reviewed_hashes_and_records_one_shot_dispatch(broker_env):
    broker, launch, _ = broker_env
    planned = broker.handle(request())
    assert broker.handle(request('manifest'))['manifest'] == planned['manifest']
    approval = request('apply', expected_manifest=planned['manifest'])
    assert broker.handle(approval)['success']
    count = launch.call_count
    with pytest.raises(ValueError, match='already dispatched'):
        broker.handle(approval)
    assert launch.call_count == count
    assert broker.metadata(IDENTIFIER)['dispatched'] == ['tfplan']


def test_broker_preserves_dispatch_evidence_after_lost_job_response(broker_env):
    broker, launch, _ = broker_env
    planned = broker.handle(request())
    launch.side_effect = TimeoutError('lost response')
    approval = request('apply', expected_manifest=planned['manifest'])
    assert broker.handle(approval)['uncertain'] is True
    with pytest.raises(ValueError):
        broker.handle(approval)
    assert launch.call_count == 2  # one plan plus one dispatch, no replay


@pytest.mark.parametrize('filename', ['main.tf', 'tfplan', '.terraform.lock.hcl', 'aca_policy_override.tf.json'])
def test_broker_refuses_changed_inputs_before_dispatch(broker_env, filename):
    broker, launch, _ = broker_env
    planned = broker.handle(request())
    (broker.root / IDENTIFIER / filename).write_text('tampered')
    with pytest.raises(ValueError):
        broker.handle(request('apply', expected_manifest=planned['manifest']))
    assert launch.call_count == 1


def test_broker_never_adopts_legacy_workspaces_and_checks_identity(broker_env):
    broker, launch, _ = broker_env
    root = paths.workspace(broker.root, IDENTIFIER, create=True)
    paths.write_file(root, 'main.tf', b'legacy state')
    with pytest.raises(ValueError, match='exists'):
        broker.handle(request())
    launch.assert_not_called()
    assert paths.read_file(root, 'main.tf') == b'legacy state'


def test_broker_rejects_identity_and_policy_change(broker_env):
    broker, launch, _ = broker_env
    broker.handle(request())
    wrong = request('manifest'); wrong['identity'] = {**IDENTITY, 'arn': 'arn:aws:iam::111111111111:user/other'}
    with pytest.raises(ValueError, match='identity'):
        broker.handle(wrong)
    broker.policy_hash = 'changed-policy'
    with pytest.raises(ValueError):
        broker.handle(request('manifest'))
    assert launch.call_count == 1


def test_artifact_helpers_reject_symlinks_hardlinks_fifos_and_escapes(tmp_path):
    root = tmp_path / 'workspaces'; root.mkdir()
    outside = tmp_path / 'outside'; outside.write_bytes(b'private')
    child = paths.workspace(root, IDENTIFIER, create=True)
    (child / 'main.tf').symlink_to(outside)
    with pytest.raises(OSError):
        paths.read_file(child, 'main.tf')
    (child / 'main.tf').unlink()
    os.link(outside, child / 'main.tf')
    with pytest.raises(ValueError):
        paths.read_file(child, 'main.tf')
    (child / 'main.tf').unlink(); os.mkfifo(child / 'main.tf')
    with pytest.raises(ValueError):
        paths.read_file(child, 'main.tf')
    for name in ('../outside', '/etc/passwd', 'nested/main.tf'):
        with pytest.raises(ValueError):
            paths.read_file(child, name)
    escaped = root / 'exec_20261103_120000_aaaaaaaa'; escaped.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        paths.workspace(root, escaped.name)


def test_artifact_size_limits_and_extra_input_rejection(tmp_path):
    root = tmp_path
    paths.write_file(root, 'ssh-key.pem', b'x' * 100)
    with pytest.raises(ValueError, match='oversized'):
        paths.read_file(root, 'ssh-key.pem', 20)
    (root / 'unreviewed.auto.tfvars').write_text('hidden = true')
    with pytest.raises(ValueError, match='Unrecognized'):
        paths.manifest(root, 'tfplan')


def test_job_environment_ignores_backend_secrets_and_cli_injection(monkeypatch):
    for name in ('GROQ_API_KEY', 'ACA_OPERATOR_PASSWORD_HASH', 'ACA_MCP_TOKEN_SHA256', 'TF_CLI_ARGS', 'TF_VAR_evil', 'AWS_PROFILE', 'LD_PRELOAD', 'HTTPS_PROXY'):
        monkeypatch.setenv(name, 'must-not-inherit')
    env = protocol.job_environment(request())
    assert not any(value == 'must-not-inherit' for value in env.values())
    assert env['AWS_SESSION_TOKEN'] == 'fake-session'
    assert env['TF_VAR_region'] == IDENTITY['region']
    assert env['TF_CLI_CONFIG_FILE'] == '/opt/aca/terraform.rc'
    assert env['HTTPS_PROXY'] == 'http://terraform-egress:3128'


def test_worker_verifies_exact_identity_without_ambient_credentials(monkeypatch):
    session = Mock(); session.client.return_value.get_caller_identity.return_value = {'Account': '222222222222', 'Arn': IDENTITY['arn']}
    session.get_partition_for_region.return_value = 'aws'
    factory = Mock(return_value=session); monkeypatch.setattr(job.boto3, 'Session', factory)
    with pytest.raises(ValueError, match='differs'):
        job.verify_identity(request(), protocol.job_environment(request()))
    assert factory.call_args.kwargs['aws_access_key_id'] == 'fake-access'
    assert factory.call_args.kwargs['aws_session_token'] == 'fake-session'


def test_logs_redact_credentials_and_pem_material():
    value = 'fake-secret fake-session -----BEGIN PRIVATE KEY-----\nprivate body\n-----END PRIVATE KEY-----'
    result = job.redact(value, request())
    assert 'fake-secret' not in result and 'fake-session' not in result and 'private body' not in result


def test_private_api_authentication_and_safe_errors():
    handler_target = Mock()
    handler_target.handle.side_effect = ValueError('secret value must never be returned')
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(handler_target, TOKEN))
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True); thread.start()
    try:
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        client.request('POST', '/v1/operation', '{}', headers={'Authorization': 'Bearer wrong'})
        assert client.getresponse().status == 401
        handler_target.handle.assert_not_called()
        client.close()
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        client.request('POST', '/v1/operation', '{"operation":"ready"}', headers={'Authorization': 'Bearer ' + TOKEN})
        response = client.getresponse(); content = response.read()
        assert response.status == 409 and b'secret value' not in content
        client.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_missing_runner_has_no_host_fallback(monkeypatch):
    monkeypatch.delenv('ACA_RUNNER_URL', raising=False)
    import subprocess
    forbidden = Mock(side_effect=AssertionError('No subprocess is allowed in the backend'))
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(runner_client.RunnerUnavailable):
        runner_client.RunnerClient().request({'operation': 'ready'})
    assert runner_client.readiness()['ready'] is False
    forbidden.assert_not_called()


@pytest.mark.parametrize('authority', ['169.254.169.254:443', 'localhost:443', 'evil.com:443', 's3.amazonaws.com:80', 's3.amazonaws.com.evil.com:443'])
def test_egress_rejects_non_aws_hosts_and_ports(authority):
    from runner.egress import destination
    with pytest.raises(ValueError):
        destination(authority)


def test_egress_rejects_private_ip_resolution(monkeypatch):
    from runner import egress
    monkeypatch.setattr(egress.socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='Private'):
        egress.destination('s3.eu-west-1.amazonaws.com:443')


@pytest.mark.parametrize('hcl', [
    'resource "local_file" "x" { filename="/app/.env" content="bad" }',
    'data "external" "x" { program=["sh"] }',
    'data "terraform_remote_state" "x" { backend="local" }',
    'output "read" { value=file("/etc/passwd") }',
    'output "read" { value=templatefile("/etc/passwd", {}) }',
    'resource "aws_iam_user" "x" { name="admin" }',
    'resource "aws_instance" "x" { user_data="arbitrary program" }',
    'resource "aws_vpc" "x" { count=1000 cidr_block="10.0.0.0/16" }',
    'import { to=aws_vpc.x id="vpc-other" }',
    'provider "aws" { profile="other" }',
    'module "hidden" { source="./module" }',
])
def test_policy_rejects_execution_filesystem_and_resource_bypasses(hcl):
    with pytest.raises(ValueError):
        validate_source(hcl, IDENTITY)


def vpc_plan(action='create', owned_id=None, owner=IDENTIFIER):
    before = {'id': owned_id, 'tags': {'ACAWorkspace': owner}} if owned_id else None
    return {'resource_changes': [{'address': 'aws_vpc.main', 'mode': 'managed', 'type': 'aws_vpc',
        'provider_name': 'registry.terraform.io/hashicorp/aws', 'change': {'actions': [action], 'before': before,
        'after': {'tags': {'ACAWorkspace': owner}, 'cidr_block': '10.0.0.0/16'} if action != 'delete' else None, 'after_unknown': {}}}]}


def test_plan_requires_proven_ownership_for_update_and_destroy():
    config = validate_source('resource "aws_vpc" "main" { cidr_block="10.0.0.0/16" }', IDENTITY)
    assert validate_plan(vpc_plan(), config, IDENTIFIER)['resources_to_add'] == 1
    with pytest.raises(ValueError, match='ledger'):
        validate_plan(vpc_plan('update', 'vpc-test'), config, IDENTIFIER)
    owned = {'aws_vpc.main': 'vpc-test'}
    assert validate_plan(vpc_plan('update', 'vpc-test'), config, IDENTIFIER, owned=owned)['resources_to_change'] == 1
    assert validate_plan(vpc_plan('delete', 'vpc-test'), config, IDENTIFIER, destroy=True, owned=owned)['resources_to_destroy'] == 1
    with pytest.raises(ValueError):
        validate_plan(vpc_plan('update', 'vpc-test', 'another-workspace'), config, IDENTIFIER, owned=owned)
    with pytest.raises(ValueError):
        validate_plan(vpc_plan(), config, IDENTIFIER, destroy=True)


def test_unknown_ownership_and_unsupported_plan_actions_fail_closed():
    config = validate_source('resource "aws_vpc" "main" { cidr_block="10.0.0.0/16" }', IDENTITY)
    value = vpc_plan(); value['resource_changes'][0]['change']['after_unknown']['tags'] = True
    with pytest.raises(ValueError):
        validate_plan(value, config, IDENTIFIER)
    value = vpc_plan(); value['resource_changes'][0]['change']['actions'] = ['delete', 'create']
    with pytest.raises(ValueError):
        validate_plan(value, config, IDENTIFIER)


def test_server_override_pins_versions_and_ownership_and_controls_key_export():
    config = validate_source('resource "aws_vpc" "main" { tags={ Name="example" } }\nresource "tls_private_key" "ssh" {\n algorithm="RSA"\n rsa_bits=4096\n}', IDENTITY)
    override = server_override(config, IDENTIFIER)
    assert override['terraform']['required_version'] == '= 1.17.1'
    assert override['terraform']['required_providers']['aws']['version'] == '= ' + VERSIONS['aws']['version']
    assert override['resource']['aws_vpc']['main']['tags'] == {'Name': 'example', 'ACAWorkspace': IDENTIFIER}
    assert override['output']['aca_private_key']['sensitive'] is True


def test_policy_applies_to_static_validation_without_terraform(monkeypatch):
    from services.terraform_service import validate_terraform_syntax
    import subprocess
    forbidden = Mock(side_effect=AssertionError('Backend must not launch Terraform'))
    monkeypatch.setattr(subprocess, 'run', forbidden)
    assert validate_terraform_syntax('provider "aws" {}')['valid'] is True
    assert validate_terraform_syntax('output "x" { value=file("/etc/passwd") }')['valid'] is False
    forbidden.assert_not_called()


def instance_case():
    hcl = '''resource "aws_instance" "main" {
      ami="ami-12345678"
      instance_type="t3.micro"
      subnet_id="subnet-approved"
      vpc_security_group_ids=["sg-approved"]
      key_name="key-approved"
      associate_public_ip_address=false
      metadata_options { http_tokens="required" }
      root_block_device { encrypted=true volume_size=8 }
    }'''.replace('encrypted=true volume_size=8', 'encrypted=true\nvolume_size=8')
    config = validate_source(hcl, IDENTITY)
    after = {'ami': 'ami-12345678', 'instance_type': 't3.micro', 'subnet_id': 'subnet-approved',
             'vpc_security_group_ids': ['sg-approved'], 'key_name': 'key-approved', 'associate_public_ip_address': False,
             'metadata_options': [{'http_tokens': 'required'}], 'root_block_device': [{'encrypted': True, 'volume_size': 8}],
             'tags': {'ACAWorkspace': IDENTIFIER}}
    plan = {'resource_changes': [{'address': 'aws_instance.main', 'mode': 'managed', 'type': 'aws_instance',
             'provider_name': 'registry.terraform.io/hashicorp/aws', 'change': {'actions': ['create'], 'before': None,
             'after': after, 'after_unknown': {}}}]}
    policy = {'allowed_amis': ['ami-12345678'], 'authorized_targets': {'aws_subnet': ['subnet-approved'],
              'aws_security_group': ['sg-approved'], 'aws_key_pair': ['key-approved']}}
    return config, plan, policy


def test_ec2_requires_approved_ami_targets_and_safe_settings():
    config, value, policy = instance_case()
    assert validate_plan(value, config, IDENTIFIER, policy)['resources_to_add'] == 1
    with pytest.raises(ValueError):
        validate_plan(value, config, IDENTIFIER)
    policy['authorized_targets']['aws_security_group'] = []
    with pytest.raises(ValueError, match='authorized'):
        validate_plan(value, config, IDENTIFIER, policy)


@pytest.mark.parametrize('field,value', [('ami', 'ami-deadbeef'), ('instance_type', 'p5.48xlarge'),
    ('associate_public_ip_address', True), ('root_block_device', [{'encrypted': False, 'volume_size': 8}]),
    ('root_block_device', [{'encrypted': True, 'volume_size': 1000}]), ('metadata_options', [{'http_tokens': 'optional'}])])
def test_ec2_security_and_cost_bounds(field, value):
    config, plan, policy = instance_case()
    plan['resource_changes'][0]['change']['after'][field] = value
    with pytest.raises(ValueError):
        validate_plan(plan, config, IDENTIFIER, policy)


def test_unknown_policy_critical_values_are_rejected():
    config, plan, policy = instance_case()
    plan['resource_changes'][0]['change']['after_unknown']['ami'] = True
    with pytest.raises(ValueError, match='Unknown'):
        validate_plan(plan, config, IDENTIFIER, policy)


def test_bucket_requires_private_versioned_encrypted_companions():
    hcl = '''resource "aws_s3_bucket" "main" { force_destroy=false }
    resource "aws_s3_bucket_public_access_block" "safe" {
      bucket=aws_s3_bucket.main.id
      block_public_acls=true
      ignore_public_acls=true
      block_public_policy=true
      restrict_public_buckets=true
    }
    resource "aws_s3_bucket_versioning" "safe" {
      bucket=aws_s3_bucket.main.id
      versioning_configuration { status="Enabled" }
    }
    resource "aws_s3_bucket_server_side_encryption_configuration" "safe" {
      bucket=aws_s3_bucket.main.id
      rule { apply_server_side_encryption_by_default { sse_algorithm="AES256" } }
    }'''
    config = validate_source(hcl, IDENTITY)
    values = {
        'aws_s3_bucket.main': {'force_destroy': False, 'tags': {'ACAWorkspace': IDENTIFIER}},
        'aws_s3_bucket_public_access_block.safe': {key: True for key in ('block_public_acls','ignore_public_acls','block_public_policy','restrict_public_buckets')},
        'aws_s3_bucket_versioning.safe': {'versioning_configuration': [{'status': 'Enabled'}]},
        'aws_s3_bucket_server_side_encryption_configuration.safe': {'rule': [{'apply_server_side_encryption_by_default': [{'sse_algorithm': 'AES256'}]}]},
    }
    value = {'resource_changes': [{'address': address, 'mode': 'managed', 'type': address.split('.')[0],
        'provider_name': 'registry.terraform.io/hashicorp/aws', 'change': {'actions': ['create'], 'after': after,
        'after_unknown': {'bucket': True} if address != 'aws_s3_bucket.main' else {}}} for address, after in values.items()]}
    assert validate_plan(value, config, IDENTIFIER)['resources_to_add'] == 4
    config['resource'] = config['resource'][:-1]
    with pytest.raises(ValueError):
        validate_plan(value, config, IDENTIFIER)


@pytest.mark.parametrize('cidr,ports,expected', [('0.0.0.0/0', (22,22), False), ('::/0',(3389,3389),False),
    ('0.0.0.0/0',(443,443),True), ('0.0.0.0/0',(80,443),False), ('10.0.0.0/24',(22,22),True)])
def test_security_group_rule_policy(cidr, ports, expected):
    from runner.policy import _rules, policy_config
    value = [{'protocol': 'tcp', 'from_port': ports[0], 'to_port': ports[1], 'cidr_blocks': [cidr]}]
    policy = policy_config({'admin_cidrs': ['10.0.0.0/24']})
    if expected:
        _rules(value, policy, True)
    else:
        with pytest.raises(ValueError):
            _rules(value, policy, True)


def test_runner_client_auth_redirect_and_output_bounds(tmp_path, monkeypatch):
    import httpx
    token = tmp_path / 'token'; token.write_text(TOKEN); token.chmod(0o600)
    real_client = httpx.Client
    captured = []
    status = [200]
    def endpoint(request):
        captured.append(request)
        if status[0] == 200:
            return httpx.Response(200, json={'ready': True})
        return httpx.Response(status[0], headers={'Location': 'https://evil.invalid'}, text='private detail')
    transport = httpx.MockTransport(endpoint)
    monkeypatch.setattr(runner_client.httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))
    client = runner_client.RunnerClient('http://terraform-runner:8090', str(token))
    assert client.request({'operation': 'ready'}) == {'ready': True}
    assert captured[-1].headers['Authorization'] == 'Bearer ' + TOKEN
    status[0] = 307
    with pytest.raises(runner_client.RunnerUnavailable) as error:
        client.request({'operation': 'ready'})
    assert 'private detail' not in str(error.value)
    assert len(captured) == 2
    with pytest.raises(runner_client.RunnerUnavailable):
        runner_client.RunnerClient('http://external.example', str(token)).request({'operation':'ready'})


def test_readiness_rejects_incorrect_job_uid_and_noninternal_network(broker_env):
    broker, _, engine = broker_env
    image = {'Id': IMAGE, 'Config': {'User': '10001:10001', 'Entrypoint': ['python','-m','runner.job']}}
    def inspect(command, **kwargs):
        if command[1] == 'version':
            return Mock(returncode=0, stdout=b'28.0.0')
        value = [image] if command[1:3] == ['image','inspect'] else [{'Internal': True}]
        return Mock(stdout=json.dumps(value).encode())
    engine.side_effect = inspect
    assert broker.ready()['ready'] is True
    image['Config']['User'] = '0:0'
    with pytest.raises(ValueError):
        broker.ready()


def test_build_configuration_is_valid_hcl_without_running_install():
    import hcl2
    from runner.install_tools import build_configuration
    cli, config = build_configuration(VERSIONS)
    assert hcl2.loads(config)['terraform']
    assert hcl2.loads(cli)['provider_installation']


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'; root.mkdir()
    monkeypatch.setattr(job, 'ROOT', root)
    canonical = tmp_path / 'canonical.lock'; canonical.write_bytes(b'image-canonical-lock')
    monkeypatch.setattr(job, 'CANONICAL_LOCK', canonical)
    monkeypatch.setattr(job, 'verify_identity', Mock())
    for name, content in {'main.tf': b'provider "aws" {}', 'aca_identity_override.tf.json': b'{}',
                          'aca_policy_override.tf.json': b'{}'}.items():
        paths.write_file(root, name, content)
    def terraform(command, env, timeout=120):
        if command[1] == 'init':
            assert '-lockfile=readonly' in command
        if command[1] == 'plan':
            name = next(value.split('=', 1)[1] for value in command if value.startswith('-out='))
            paths.write_file(root, name, b'reviewed plan')
        if command[1] == 'show':
            return 0, json.dumps({'resource_changes': []} if len(command) == 4 else {'values': {'root_module': {'resources': []}}})
        return 0, 'No changes or complete'
    run = Mock(side_effect=terraform)
    monkeypatch.setattr(job, 'run', run)
    return root, run


def job_body(operation='plan', **fields):
    return {**request(operation, **fields), 'policy': {}, 'owned': {}}


def test_job_plans_with_canonical_lock_then_applies_reviewed_hashes(job_env):
    root, run = job_env
    assert job.execute(job_body())['success']
    assert paths.read_file(root, '.terraform.lock.hcl') == b'image-canonical-lock'
    expected = {**paths.manifest(root, 'tfplan'), '__runner_image': IMAGE, '__policy': 'policy', '__ownership': 'ledger'}
    result = job.execute(job_body('apply', expected_manifest=expected))
    assert result['success'] and result['owned'] == {}
    assert any(call.args[0] == ['terraform', 'apply', '-input=false', '-no-color', 'tfplan'] for call in run.call_args_list)


def test_job_refuses_changed_lock_without_deleting_it(job_env):
    root, run = job_env
    paths.write_file(root, '.terraform.lock.hcl', b'tampered lock')
    with pytest.raises(ValueError, match='lockfile differs'):
        job.execute(job_body())
    run.assert_not_called()
    assert paths.read_file(root, '.terraform.lock.hcl') == b'tampered lock'


def test_job_rechecks_hashes_after_structured_plan_inspection(job_env):
    root, run = job_env
    job.execute(job_body())
    expected = paths.manifest(root, 'tfplan')
    original = run.side_effect
    def changed(command, env, timeout=120):
        result = original(command, env, timeout)
        if command[1] == 'show':
            (root / 'tfplan').write_bytes(b'changed after inspection')
        return result
    run.side_effect = changed; run.reset_mock()
    with pytest.raises(ValueError, match='changed'):
        job.execute(job_body('apply', expected_manifest=expected))
    assert not any(call.args[0][1] == 'apply' for call in run.call_args_list)


def test_job_timeout_after_dispatch_is_uncertain(job_env):
    root, run = job_env
    job.execute(job_body())
    expected = paths.manifest(root, 'tfplan')
    original = run.side_effect
    def timeout(command, env, timeout=120):
        if command[1] == 'apply':
            raise TimeoutError('lost response')
        return original(command, env, timeout)
    run.side_effect = timeout
    result = job.execute(job_body('apply', expected_manifest=expected))
    assert result['success'] is False and result['uncertain'] is True


def test_job_policy_failure_never_reaches_apply(job_env):
    root, run = job_env
    job.execute(job_body())
    expected = paths.manifest(root, 'tfplan')
    original = run.side_effect
    def unsafe(command, env, timeout=120):
        if command[1] == 'show':
            return 0, json.dumps({'resource_changes': [{'type': 'aws_iam_user', 'mode': 'managed'}]})
        return original(command, env, timeout)
    run.side_effect = unsafe; run.reset_mock()
    with pytest.raises(ValueError):
        job.execute(job_body('apply', expected_manifest=expected))
    assert not any(call.args[0][1] == 'apply' for call in run.call_args_list)


def test_token_bootstrap_is_explicit_private_and_idempotent(tmp_path, monkeypatch, capsys):
    from runner import token
    path = tmp_path / 'token'
    monkeypatch.setattr(token, 'TOKEN_PATH', path)
    monkeypatch.setattr(token.sys, 'argv', ['token'])
    token.main(); value = protocol.load_token(path)
    assert len(value) == 64 and path.stat().st_mode & 0o777 == 0o400
    token.main(); assert protocol.load_token(path) == value
    assert value not in capsys.readouterr().out
    monkeypatch.setattr(token.sys, 'argv', ['token', '--rotate'])
    token.main(); assert protocol.load_token(path) != value


def test_token_loader_rejects_links_world_readability_and_oversize(tmp_path):
    value = tmp_path / 'token'; value.write_text(TOKEN); value.chmod(0o600)
    link = tmp_path / 'link'; link.symlink_to(value)
    with pytest.raises(OSError):
        protocol.load_token(link)
    value.chmod(0o644)
    with pytest.raises(ValueError):
        protocol.load_token(value)
    value.chmod(0o600); value.write_text('x' * 1000)
    with pytest.raises(ValueError):
        protocol.load_token(value)


def test_job_dispatch_rechecks_internal_network_contract(broker_env):
    broker, launch, engine = broker_env
    original = engine.side_effect
    def external_network(command, **kwargs):
        if command[1:3] == ['network', 'inspect']:
            return Mock(stdout=b'[{"Internal":false}]')
        return original(command, **kwargs)
    engine.side_effect = external_network
    with pytest.raises(ValueError, match='internal'):
        broker.handle(request())
    launch.assert_not_called()


def test_completed_key_export_remains_readable_after_policy_image_upgrade(broker_env):
    broker, _, _ = broker_env
    broker.handle(request())
    paths.write_file(broker.root / IDENTIFIER, 'ssh-key.pem', b'fixture-private-key')
    broker.image = 'sha256:' + 'c' * 64
    broker.policy_hash = 'new-policy'
    result = broker.handle(request('key'))
    assert base64.b64decode(result['content_base64']) == b'fixture-private-key'
    with pytest.raises(ValueError):
        broker.handle(request('manifest'))


@pytest.mark.parametrize('extra', [
    'locals { leak = tls_private_key.ssh }',
    'output "leak" { value=tls_private_key.ssh.private_key_pem }',
    'resource "aws_vpc" "leak" { tags={ secret=tls_private_key.ssh.private_key_pem } }',
    'locals { key = tls_private_key.ssh["${join("_", ["private", "key", "pem"])}"] }',
])
def test_private_key_aliases_and_resource_exfiltration_are_rejected(extra):
    source = 'resource "tls_private_key" "ssh" {\nalgorithm="RSA"\nrsa_bits=4096\n}\n' + extra
    with pytest.raises(ValueError):
        validate_source(source, IDENTITY)


def test_controlled_public_key_attachment_remains_supported():
    source = '''resource "tls_private_key" "ssh" {
      algorithm="RSA"
      rsa_bits=4096
    }
    resource "aws_key_pair" "main" { public_key=tls_private_key.ssh.public_key_openssh }'''
    assert validate_source(source, IDENTITY)['resource']


def test_manifest_requires_image_policy_and_ownership_evidence():
    from runner.protocol import validate_manifest
    files = {name: 'a' * 64 for name in ('main.tf','aca_identity_override.tf.json','aca_policy_override.tf.json','.terraform.lock.hcl','tfplan')}
    with pytest.raises(ValueError):
        validate_manifest(files, 'tfplan')
    value = {**files, '__runner_image': IMAGE, '__policy': 'b' * 64, '__ownership': 'c' * 64}
    assert validate_manifest(value, 'tfplan') == value
    value['outside.tf'] = 'a' * 64
    with pytest.raises(ValueError):
        validate_manifest(value, 'tfplan')


def test_old_engine_cannot_fall_back_to_a_full_volume_mount(broker_env):
    broker, launch, engine = broker_env
    engine.return_value = Mock(stdout=b'25.0.0')
    engine.side_effect = None
    with pytest.raises(ValueError, match='26 or newer'):
        broker.handle(request())
    launch.assert_not_called()


def test_durable_outcome_inspection_never_launches_or_replays(broker_env):
    broker, launch, _ = broker_env
    broker.handle(request())
    hashes = broker.handle(request('manifest'))['manifest']
    applied = broker.handle(request('apply', expected_manifest=hashes))
    assert applied['success']
    launch.reset_mock()
    outcome = broker.handle(request('outcome', plan_file='tfplan'))
    assert outcome['dispatched'] is True
    assert outcome['outcome']['status'] == 'complete'
    assert outcome['outcome']['result']['success'] is True
    launch.assert_not_called()
    with pytest.raises(ValueError, match='already dispatched'):
        broker.handle(request('apply', expected_manifest=hashes))
    launch.assert_not_called()


def test_interrupted_runner_persists_uncertain_evidence(broker_env):
    broker, launch, _ = broker_env
    broker.handle(request())
    hashes = broker.handle(request('manifest'))['manifest']
    launch.side_effect = TimeoutError('private timeout')
    assert broker.handle(request('apply', expected_manifest=hashes))['uncertain']
    outcome = broker.handle(request('outcome', plan_file='tfplan'))
    assert outcome['outcome']['status'] == 'uncertain'
    assert 'private timeout' not in json.dumps(outcome)
