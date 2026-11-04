"""Fixed runner protocol; no caller-controlled commands, images or mount paths."""
import re
from runner.paths import workspace_id, artifact_name

OPERATIONS = {'plan', 'destroy_plan', 'apply', 'manifest', 'key', 'ready', 'outcome'}
MAX_REQUEST = 2 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024


def validate_request(body):
    if not isinstance(body, dict) or body.get('operation') not in OPERATIONS:
        raise ValueError('Unsupported runner operation')
    allowed = {'operation', 'workspace_id', 'identity', 'credentials', 'hcl', 'plan_file', 'expected_manifest', 'state_backend'}
    if set(body) - allowed:
        raise ValueError('Unsupported runner argument')
    operation = body['operation']
    if operation == 'ready':
        if set(body) != {'operation'}:
            raise ValueError('Readiness does not accept job arguments')
        return body
    workspace_id(body.get('workspace_id'))
    identity = body.get('identity', {})
    if set(identity) != {'account_id', 'region', 'partition', 'arn'} or not re.fullmatch(r'\d{12}', str(identity.get('account_id', ''))) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', str(identity.get('region', ''))):
        raise ValueError('A verified account identity is required')
    if not isinstance(identity.get('arn'), str) or len(identity['arn'].split(':')) < 6 or identity['partition'] not in ('aws', 'aws-us-gov', 'aws-cn') or not identity['arn'].startswith(f"arn:{identity['partition']}:") or identity['arn'].split(':')[4] != identity['account_id']:
        raise ValueError('Invalid identity ARN')
    if operation in ('plan', 'destroy_plan', 'apply'):
        credentials = body.get('credentials')
        if not isinstance(credentials, dict) or set(credentials) - {'access_key', 'secret_key', 'token'} or not all(isinstance(credentials.get(k), str) and 1 <= len(credentials[k]) <= 1024 for k in ('access_key', 'secret_key')) or not isinstance(credentials.get('token', ''), str) or len(credentials.get('token', '')) > 8192:
            raise ValueError('Frozen operation credentials are required')
    elif 'credentials' in body:
        raise ValueError('Artifact operations do not accept credentials')
    if operation == 'plan':
        if not isinstance(body.get('hcl'), str) or not 1 <= len(body['hcl'].encode()) <= 256 * 1024:
            raise ValueError('Configuration exceeds size limit or is missing')
    elif 'hcl' in body or 'state_backend' in body:
        raise ValueError('Only new plans accept configuration')
    if operation in ('destroy_plan', 'apply', 'manifest', 'outcome'):
        name = body.get('plan_file')
        artifact_name(name)
        if name != 'tfplan' and not str(name).startswith('destroy_'):
            raise ValueError('A saved plan artifact is required')
    if operation == 'destroy_plan' and not body['plan_file'].startswith('destroy_'):
        raise ValueError('Destroy plans require a separate artifact')
    if operation == 'apply' and (not isinstance(body.get('expected_manifest'), dict) or not body['expected_manifest']):
        raise ValueError('Reviewed artifact hashes are required')
    return body


def job_environment(body):
    credentials, identity = body['credentials'], body['identity']
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8',
           'CHECKPOINT_DISABLE': '1', 'TF_IN_AUTOMATION': '1', 'TF_INPUT': '0', 'TF_CLI_CONFIG_FILE': '/opt/aca/terraform.rc',
           'AWS_ACCESS_KEY_ID': credentials['access_key'], 'AWS_SECRET_ACCESS_KEY': credentials['secret_key'],
           'AWS_REGION': identity['region'], 'AWS_DEFAULT_REGION': identity['region'],
           'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_CONFIG_FILE': '/dev/null', 'AWS_SHARED_CREDENTIALS_FILE': '/dev/null',
           'HTTPS_PROXY': 'http://terraform-egress:3128', 'HTTP_PROXY': 'http://terraform-egress:3128',
           'NO_PROXY': '', 'TF_VAR_region': identity['region']}
    if credentials.get('token'):
        env['AWS_SESSION_TOKEN'] = credentials['token']
    return env


def load_token(path):
    import os
    import stat
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 64 <= info.st_size <= 66 or stat.S_IMODE(info.st_mode) & 0o007:
            raise ValueError('Runner token must be a private regular file')
        value = os.read(descriptor, 67).decode().strip()
    finally:
        os.close(descriptor)
    if not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('Runner token must be a separately generated 32-byte hex secret')
    return value


def validate_manifest(value, plan_file):
    required = {'main.tf', 'aca_identity_override.tf.json', 'aca_policy_override.tf.json', '.terraform.lock.hcl', plan_file,
                '__runner_image', '__policy', '__ownership'}
    if not isinstance(value, dict) or set(value) not in (required, required | {'aca_backend.tf.json'}):
        raise ValueError('Runner manifest does not contain the required integrity evidence')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(value.get('__runner_image'))):
        raise ValueError('Runner image evidence is invalid')
    if not all(isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item) for key, item in value.items() if key != '__runner_image'):
        raise ValueError('Runner artifact/policy/ownership hashes are invalid')
    return value
