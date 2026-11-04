"""Fake runner transport for application tests; never executes a host command."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from runner.paths import manifest, workspace, write_file, read_file
from runner.policy import validate_source
from runner.protocol import validate_request, job_environment


class FakeRunner:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir()
        self.calls, self.plans = [], {}
        self.run = Mock(side_effect=self.execute)

    def execute(self, command, **kwargs):
        for value in command:
            if value.startswith('-out='):
                (kwargs['cwd'] / value.split('=', 1)[1]).write_bytes(b'fake saved plan')
        return SimpleNamespace(returncode=0, stdout='Plan: 1 to add, 0 to change, 0 to destroy.', stderr='')

    def hashes(self, root, plan_file):
        return {**manifest(root, plan_file), '__runner_image': 'sha256:' + 'a' * 64,
                '__policy': 'b' * 64, '__ownership': 'c' * 64}

    def request(self, body):
        validate_request(body)
        self.calls.append(body)
        operation = body['operation']
        if operation == 'ready':
            return {'ready': True, 'policy': 'test-policy', 'terraform_version': '1.17.1'}
        identifier = body['workspace_id']
        if operation == 'plan':
            validate_source(body['hcl'], body['identity'])
            root = workspace(self.root, identifier, create=True)
            for name, content in {
                'main.tf': body['hcl'].encode(), 'aca_policy_override.tf.json': b'{}', '.terraform.lock.hcl': b'test-only lock',
                'aca_identity_override.tf.json': json.dumps({'provider': {'aws': [{'region': body['identity']['region'], 'allowed_account_ids': [body['identity']['account_id']]}]}}).encode(),
            }.items():
                write_file(root, name, content)
        else:
            root = workspace(self.root, identifier)
        plan_file = body.get('plan_file', 'tfplan')
        if operation == 'key':
            return {'content_base64': base64.b64encode(read_file(root, 'ssh-key.pem', 65536)).decode()}
        if operation == 'manifest':
            actual = self.hashes(root, plan_file)
            if self.plans[(identifier, plan_file)] != actual:
                raise ValueError('Fake runner observed changed artifacts')
            return {'manifest': actual}
        if operation == 'apply':
            if self.hashes(root, plan_file) != body['expected_manifest']:
                raise ValueError('Changed plan')
            try:
                result = self.run(['terraform', 'apply', '-no-color', plan_file], cwd=root, env=job_environment(body), timeout=300)
            except Exception:
                return {'success': False, 'uncertain': True, 'apply_output': 'Runner execution response unavailable'}
            return {'success': result.returncode == 0, 'apply_output': result.stdout + result.stderr, 'resources_applied': [], 'key_files': []}
        init = self.run(['terraform', 'init', '-lockfile=readonly'], cwd=root, env=job_environment(body), timeout=120)
        if init.returncode:
            return {'success': False, 'plan_output': 'Initialization failed'}
        command = ['terraform', 'plan', '-out=' + plan_file, '-no-color']
        if operation == 'destroy_plan':
            command.append('-destroy')
        result = self.run(command, cwd=root, env=job_environment(body), timeout=120)
        if result.returncode:
            return {'success': False, 'plan_output': result.stdout + result.stderr}
        self.plans[(identifier, plan_file)] = self.hashes(root, plan_file)
        return {'success': True, 'plan_output': result.stdout + result.stderr,
                'resources_to_add': 1, 'resources_to_change': 0, 'resources_to_destroy': 0}
