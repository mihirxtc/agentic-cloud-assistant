"""Trusted private runner API. Only this service may access the container engine."""
import base64
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import uuid

from runner.paths import workspace, workspace_id, read_file, write_file, manifest
from runner.policy import POLICY_VERSION, VERSIONS, validate_source, server_override, policy_config
from runner.protocol import validate_request, MAX_OUTPUT, MAX_REQUEST, load_token


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def run_container(command, secret_input, timeout=440):
    # No credentials are placed in argv, the Docker container configuration or files.
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks, size, errors = [], [0], []
    overflow = threading.Event()
    count_lock = threading.Lock()
    def read(stream, destination):
        while chunk := stream.read(65536):
            with count_lock:
                size[0] += len(chunk)
                too_large = size[0] > MAX_OUTPUT
            if too_large:
                overflow.set(); process.kill(); return
            destination.append(chunk)
    threads = [threading.Thread(target=read, args=(process.stdout, chunks), daemon=True),
               threading.Thread(target=read, args=(process.stderr, errors), daemon=True)]
    for thread in threads:
        thread.start()
    def send():
        try:
            process.stdin.write(secret_input); process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    sender = threading.Thread(target=send, daemon=True); sender.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait()
        raise TimeoutError('Runner deadline exceeded') from None
    finally:
        for thread in threads:
            thread.join(timeout=5)
        sender.join(timeout=1)
    if overflow.is_set() or process.returncode:
        raise ValueError('Container execution failed or exceeded output limits')
    result = json.loads(b''.join(chunks))
    if not isinstance(result, dict) or not isinstance(result.get('success'), bool):
        raise ValueError('Invalid job response')
    return result


class Broker:
    def __init__(self, root, control, image, volume, network, config):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise ValueError('Configure an immutable, locally built job image ID')
        if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value) for value in (volume, network)):
            raise ValueError('Invalid deployment volume/network')
        if set(config) - {'resource_policy', 'state_backend'}:
            raise ValueError('Unsupported runner configuration')
        self.policy = policy_config(config.get('resource_policy'))
        self.backend = config.get('state_backend') or None
        if self.backend is not None and (set(self.backend) != {'bucket', 'region'} or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.backend['bucket']) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', self.backend['region'])):
            raise ValueError('Invalid server S3 state configuration')
        self.root, self.control = Path(root), Path(control)
        self.image, self.volume, self.network = image, volume, network
        self.policy_hash = digest({'config': config, 'versions': VERSIONS, 'policy': POLICY_VERSION})
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.control.mkdir(mode=0o700, parents=True, exist_ok=True)

    def metadata(self, identifier):
        workspace_id(identifier)
        descriptor = os.open(self.control / (identifier + '.json'), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Unsafe runner metadata')
            return json.loads(source.read(2 * MAX_REQUEST))

    def save(self, identifier, value):
        name = self.control / (workspace_id(identifier) + '.json')
        temporary = self.control / (uuid.uuid4().hex + '.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, name)
        directory = os.open(self.control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)  # Dispatch evidence must survive a crash before we launch the job.
        finally:
            os.close(directory)

    def hashes(self, identifier, plan_file, meta):
        result = manifest(workspace(self.root, identifier), plan_file)
        result.update(__runner_image=self.image, __policy=self.policy_hash, __ownership=digest(meta['owned']))
        return result

    def command(self, identifier, name):
        return ['docker', 'run', '--rm', '-i', '--pull=never', '--name', name,
                '--user', '10001:10001', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--pids-limit=64', '--memory=768m', '--memory-swap=768m', '--cpus=1',
                '--network', self.network, '--dns=127.0.0.1',
                '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=128m,uid=10001,gid=10001',
                '--mount', f'type=volume,source={self.volume},target=/workspace,volume-subpath={workspace_id(identifier)}', self.image]

    def ready(self):
        version = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True, timeout=10, check=True)
        if not re.match(r'\d+\.', version.stdout.decode().strip()) or int(version.stdout.decode().strip().split('.')[0]) < 26:
            raise ValueError('Docker Engine 26 or newer is required for isolated workspace subpaths')
        result = subprocess.run(['docker', 'image', 'inspect', self.image], capture_output=True, timeout=10, check=True)
        image = json.loads(result.stdout)[0]
        if image.get('Id') != self.image or image.get('Config', {}).get('User') != '10001:10001' or image['Config'].get('Entrypoint') != ['python', '-m', 'runner.job']:
            raise ValueError('Job image does not satisfy runner contract')
        result = subprocess.run(['docker', 'network', 'inspect', self.network], capture_output=True, timeout=10, check=True)
        if json.loads(result.stdout)[0].get('Internal') is not True:
            raise ValueError('Job network must be internal')
        subprocess.run(['docker', 'volume', 'inspect', self.volume], capture_output=True, timeout=10, check=True)
        return {'ready': True, 'policy': POLICY_VERSION, 'image': self.image, 'terraform_version': VERSIONS['terraform']['version']}

    def handle(self, body):
        validate_request(body)
        if body['operation'] == 'ready':
            return self.ready()
        identifier, operation = body['workspace_id'], body['operation']
        if operation in ('plan', 'destroy_plan', 'apply'):
            self.ready()  # Revalidate image UID/entrypoint and internal networking before every job.
        lock = os.open(self.control / (identifier + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._handle(body, identifier, operation)
        except BlockingIOError:
            raise ValueError('Workspace already has an active runner request') from None
        finally:
            os.close(lock)

    def _handle(self, body, identifier, operation):
        if operation == 'plan':
            # Never adopt legacy state or files lacking runner ownership metadata.
            if (self.control / (identifier + '.json')).exists() or (self.root / identifier).exists():
                raise ValueError('Workspace exists; request a new proposal instead of overwriting artifacts')
            if body.get('state_backend') != self.backend:
                raise ValueError('State backend differs from runner deployment configuration')
            config = validate_source(body['hcl'], body['identity'], self.policy)
            root = workspace(self.root, identifier, create=True)
            write_file(root, 'main.tf', body['hcl'].encode())
            write_file(root, 'aca_identity_override.tf.json', json.dumps({'provider': {'aws': [{'region': body['identity']['region'], 'allowed_account_ids': [body['identity']['account_id']]}]}}).encode())
            write_file(root, 'aca_policy_override.tf.json', json.dumps(server_override(config, identifier)).encode())
            if self.backend:
                write_file(root, 'aca_backend.tf.json', json.dumps({'terraform': {'backend': {'s3': {
                    **self.backend, 'key': identifier + '/terraform.tfstate', 'use_lockfile': True, 'encrypt': True,
                    'allowed_account_ids': [body['identity']['account_id']]}}}}).encode())
            meta = {'identity': body['identity'], 'owned': {}, 'plans': {}, 'policy_hash': self.policy_hash, 'image': self.image, 'dispatched': []}
            self.save(identifier, meta)
        else:
            root = workspace(self.root, identifier)
            meta = self.metadata(identifier)
        if meta['identity'] != body['identity']:
            raise ValueError('Workspace identity differs from the recorded operation')
        if operation == 'outcome':
            return {'dispatched': body['plan_file'] in meta['dispatched'],
                    'outcome': meta.get('outcomes', {}).get(body['plan_file'])}
        if operation == 'key':
            return {'content_base64': base64.b64encode(read_file(root, 'ssh-key.pem', 65536)).decode()}
        if meta['policy_hash'] != self.policy_hash or meta['image'] != self.image:
            raise ValueError('Workspace policy/image changed; validate recovery and replan')
        if operation == 'manifest':
            current = self.hashes(identifier, body['plan_file'], meta)
            if meta['plans'].get(body['plan_file']) != current:
                raise ValueError('Artifact differs from inspected saved plan')
            return {'manifest': current}
        plan_file = body.get('plan_file', 'tfplan')
        if operation == 'apply':
            if meta['plans'].get(plan_file) != body['expected_manifest'] or self.hashes(identifier, plan_file, meta) != body['expected_manifest']:
                raise ValueError('Approval hashes differ from inspected artifacts')
            if plan_file in meta['dispatched']:
                raise ValueError('Saved plan was already dispatched; reconcile outcome before further action')
            # Persist before launching: a broker crash/lost HTTP response cannot replay apply.
            meta['dispatched'].append(plan_file)
            meta.setdefault('outcomes', {})[plan_file] = {'status': 'running'}
            self.save(identifier, meta)
        if operation == 'destroy_plan' and plan_file in meta['plans']:
            raise ValueError('Destroy plan already exists')
        name = 'aca-job-' + uuid.uuid4().hex
        job = {**body, 'policy': self.policy, 'owned': meta['owned']}
        # Only this broker sets policy/ownership. They are not accepted in the public request schema.
        try:
            result = run_container(self.command(identifier, name), json.dumps(job).encode())
        except Exception:
            result = {'success': False, 'uncertain': operation == 'apply', 'error': 'Runner execution unavailable or interrupted'}
        finally:
            # Never stop/reuse another job name; Docker CLI arguments are fixed, not shell text.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15, check=False)
        if result.get('success'):
            if operation == 'apply':
                owned = result.pop('owned', None)
                if not isinstance(owned, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in owned.items()):
                    return {'success': False, 'uncertain': True, 'error': 'Ownership evidence unavailable after execution'}
                meta['owned'] = owned
            else:
                meta['plans'][plan_file] = self.hashes(identifier, plan_file, meta)
                result['manifest'] = meta['plans'][plan_file]
            self.save(identifier, meta)
        if operation == 'apply':
            meta.setdefault('outcomes', {})[plan_file] = {'status': 'complete' if result.get('success') else 'uncertain', 'result': result}
            self.save(identifier, meta)
        return result


def token_from_file(path):
    return load_token(path)


def make_handler(broker, token):
    slots = threading.BoundedSemaphore(2)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not echo request data, credentials or authentication tokens.

        def do_POST(self):
            self.connection.settimeout(15)
            if self.path != '/v1/operation' or not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.reply(401, {'error': 'Runner authentication required'}); return
            if not slots.acquire(blocking=False):
                self.reply(429, {'error': 'Runner busy'}); return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_REQUEST or self.headers.get('Transfer-Encoding'):
                    self.reply(413, {'error': 'Invalid runner request size'}); return
                body = json.loads(self.rfile.read(length))
                result = broker.handle(body)
                self.reply(200, result)
            except FileNotFoundError:
                self.reply(404, {'error': 'Runner artifact not found'})
            except Exception:
                self.reply(409, {'error': 'Runner request rejected or execution unavailable; inspect action history'})
            finally:
                slots.release()

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store'); self.end_headers()
            self.wfile.write(data)
    return Handler


def main():
    config = json.loads(Path(os.environ['ACA_RUNNER_CONFIG']).read_text())
    broker = Broker('/workspaces', '/control', os.environ['ACA_JOB_IMAGE'], os.environ['ACA_WORKSPACE_VOLUME'],
                    os.environ['ACA_JOB_NETWORK'], config)
    token = token_from_file('/run/credentials/token')
    ThreadingHTTPServer(('0.0.0.0', 8090), make_handler(broker, token)).serve_forever()


if __name__ == '__main__':
    main()
