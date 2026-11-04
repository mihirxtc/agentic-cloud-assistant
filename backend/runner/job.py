"""Fixed container entrypoint. Credentials arrive over stdin, never container env/config."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import boto3
from botocore.config import Config

from runner.paths import manifest, read_file, write_file
from runner.policy import validate_source, validate_plan
from runner.protocol import MAX_OUTPUT, MAX_REQUEST, job_environment

ROOT = Path('/workspace')
CANONICAL_LOCK = Path('/opt/aca/provider.lock.hcl')


def run(command, env, timeout=120):
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    output, size, overflow = [], [0], threading.Event()
    def collect():
        while chunk := process.stdout.read(65536):
            size[0] += len(chunk)
            if size[0] > MAX_OUTPUT:
                overflow.set(); os.killpg(process.pid, signal.SIGKILL); return
            output.append(chunk)
    thread = threading.Thread(target=collect, daemon=True); thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL); process.wait(); thread.join()
        raise TimeoutError('Terraform operation timed out') from None
    thread.join()
    if overflow.is_set():
        raise ValueError('Terraform output exceeds limit')
    return process.returncode, b''.join(output).decode('utf-8', errors='replace')


def verify_identity(body, env):
    credentials, identity = body['credentials'], body['identity']
    session = boto3.Session(aws_access_key_id=credentials['access_key'], aws_secret_access_key=credentials['secret_key'],
                            aws_session_token=credentials.get('token') or None, region_name=identity['region'])
    # Passing proxies explicitly avoids dependence on the wrapper's environment.
    caller = session.client('sts', config=Config(connect_timeout=5, read_timeout=10,
        retries={'total_max_attempts': 1}, proxies={'https': env['HTTPS_PROXY']})).get_caller_identity()
    if caller.get('Account') != identity['account_id'] or caller.get('Arn') != identity['arn'] or session.get_partition_for_region(identity['region']) != identity['partition']:
        raise ValueError('Worker identity differs from the frozen request identity')


def _structured(plan_file, env):
    code, output = run(['terraform', 'show', '-json', plan_file], env)
    if code:
        raise ValueError('Saved plan cannot be inspected')
    return json.loads(output)


def execute(body):
    env = job_environment(body)
    verify_identity(body, env)
    source = read_file(ROOT, 'main.tf', 256 * 1024).decode()
    config = validate_source(source, body['identity'], body['policy'])
    operation, plan_file = body['operation'], body.get('plan_file', 'tfplan')
    if operation == 'apply':
        expected_files = {key: value for key, value in body['expected_manifest'].items() if not key.startswith('__')}
        if manifest(ROOT, plan_file) != expected_files:
            raise ValueError('Reviewed plan artifacts changed')
        plan = _structured(plan_file, env)
        validate_plan(plan, config, body['workspace_id'], body['policy'], plan_file.startswith('destroy_'), body['owned'])
        # Recheck immediately before dispatch. Job root is isolated from other jobs.
        if manifest(ROOT, plan_file) != expected_files:
            raise ValueError('Reviewed plan artifacts changed')
        try:
            code, output = run(['terraform', 'apply', '-input=false', '-no-color', plan_file], env, timeout=300)
        except Exception:
            return {'success': False, 'uncertain': True, 'apply_output': 'Execution interrupted after dispatch; inspect remote state'}
        result = {'success': code == 0, 'apply_output': redact(output, body), 'resources_applied': [], 'key_files': []}
        if code == 0:
            status, state_output = run(['terraform', 'show', '-json'], env)
            if status:
                return {'success': False, 'uncertain': True, 'apply_output': 'Apply ended but ownership evidence could not be collected'}
            state = json.loads(state_output)
            state_resources = state.get('values', {}).get('root_module', {}).get('resources', [])
            result['owned'] = {item['address']: item.get('values', {}).get('id') for item in state_resources}
            if 'tls_private_key.ssh' in result['owned']:
                code, private_key = run(['terraform', 'output', '-raw', 'aca_private_key'], env)
                if code:
                    return {'success': False, 'uncertain': True, 'apply_output': 'Apply ended but key export failed'}
                # Export exactly one fixed artifact, without following or replacing existing files.
                try:
                    write_file(ROOT, 'ssh-key.pem', private_key.encode())
                except FileExistsError:
                    if read_file(ROOT, 'ssh-key.pem', 65536) != private_key.encode():
                        return {'success': False, 'uncertain': True, 'apply_output': 'Key artifact changed; inspect execution evidence'}
                result['key_files'] = [{'name': 'ssh-key.pem'}]
        return result
    init = ['terraform', 'init', '-input=false', '-no-color']
    if not (ROOT / 'aca_backend.tf.json').exists():
        init.append('-backend=false')
    canonical = CANONICAL_LOCK.read_bytes()
    try:
        lock = read_file(ROOT, '.terraform.lock.hcl')
        if lock != canonical:
            raise ValueError('Provider lockfile differs from the pinned job image; replan after upgrade')
    except FileNotFoundError:
        if operation != 'plan':
            raise ValueError('Provider lockfile is missing')
        write_file(ROOT, '.terraform.lock.hcl', canonical)
    # The image contains the exact providers and a canonical lockfile. Never upgrade/rewrite it.
    init.append('-lockfile=readonly')
    code, output = run(init, env)
    if code:
        return {'success': False, 'plan_output': 'Provider/backend initialization failed; lockfile was retained'}
    command = ['terraform', 'plan', '-input=false', '-out=' + plan_file, '-no-color']
    if operation == 'destroy_plan':
        command.append('-destroy')
    code, output = run(command, env)
    if code:
        return {'success': False, 'plan_output': redact(output, body)}
    counts = validate_plan(_structured(plan_file, env), config, body['workspace_id'], body['policy'], operation == 'destroy_plan', body['owned'])
    return {'success': True, 'plan_output': redact(output, body), **counts}


def redact(output, body):
    for value in body['credentials'].values():
        if value:
            output = output.replace(value, '[redacted]')
    # Terraform normally marks sensitive values; never return PEM blocks if a provider prints one.
    import re
    return re.sub(r'-----BEGIN [^-]+-----.*?-----END [^-]+-----', '[redacted key]', output, flags=re.S)


def main():
    body = json.loads(sys.stdin.buffer.read(MAX_REQUEST + 1))
    try:
        result = execute(body)
    except Exception as error:
        result = {'success': False, 'uncertain': body.get('operation') == 'apply',
                  'error': str(error) if type(error) is ValueError else 'Runner job could not complete validation/execution; inspect recorded status'}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
