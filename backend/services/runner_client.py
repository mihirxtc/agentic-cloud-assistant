"""Authenticated private runner client. There is deliberately no local execution fallback."""
import json
import os
from urllib.parse import urlsplit

import httpx

from runner.protocol import MAX_OUTPUT, validate_request, load_token
from services.aws_connection import get_connection


class RunnerUnavailable(ValueError):
    pass


class RunnerClient:
    def __init__(self, url=None, token_file=None):
        self.url = (url if url is not None else os.getenv('ACA_RUNNER_URL', '')).rstrip('/')
        self.token_file = token_file or os.getenv('ACA_RUNNER_TOKEN_FILE', '/run/credentials/token')

    def request(self, body):
        validate_request(body)
        target = urlsplit(self.url)
        if target.scheme not in ('http', 'https') or target.username or target.password or target.query or target.fragment or target.path not in ('', '/') or not target.hostname:
            raise RunnerUnavailable('Configure the isolated Terraform runner URL')
        if target.scheme == 'http' and target.hostname not in ('terraform-runner', 'localhost', '127.0.0.1', '::1'):
            raise RunnerUnavailable('Remote runners require HTTPS')
        try:
            token = load_token(self.token_file)
            with httpx.Client(timeout=httpx.Timeout(480, connect=5), trust_env=False, follow_redirects=False) as client:
                with client.stream('POST', self.url + '/v1/operation', json=body,
                                   headers={'Authorization': 'Bearer ' + token}) as response:
                    if response.status_code == 404:
                        raise FileNotFoundError('Runner artifact not found')
                    if response.status_code != 200:
                        raise RunnerUnavailable('Runner rejected the operation or is unavailable; inspect execution history')
                    parts, size = [], 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > MAX_OUTPUT:
                            raise RunnerUnavailable('Runner response exceeds the output limit')
                        parts.append(part)
            result = json.loads(b''.join(parts))
            if not isinstance(result, dict):
                raise RunnerUnavailable('Invalid runner response')
            return result
        except (FileNotFoundError, RunnerUnavailable):
            raise
        except Exception:
            raise RunnerUnavailable('Isolated Terraform runner unavailable; execution did not fall back to this host') from None


def identity(value):
    return {key: value[key] for key in ('account_id', 'region', 'partition', 'arn')}


def operation(operation_name, workspace_id=None, *, aws_identity=None, credentials=False, **fields):
    body = {'operation': operation_name, **fields}
    if workspace_id is not None:
        connection = get_connection() if aws_identity is None or credentials else None
        body.update(workspace_id=workspace_id, identity=identity(aws_identity or connection.identity))
        if credentials:
            body['credentials'] = {'access_key': connection.access_key, 'secret_key': connection.secret_key,
                                   'token': connection.token or ''}
    return RunnerClient().request(body)


def readiness():
    try:
        result = operation('ready')
        return {'ready': result.get('ready') is True, 'execution': 'isolated-runner',
                'terraform_version': result.get('terraform_version'), 'policy': result.get('policy')}
    except Exception:
        return {'ready': False, 'execution': 'isolated-runner', 'error': 'Terraform runner unavailable or not configured'}
