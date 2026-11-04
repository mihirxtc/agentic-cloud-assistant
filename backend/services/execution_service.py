"""Terraform operations are delegated to an authenticated isolated runner only."""
import base64
import os
import time
import uuid

from runner.paths import workspace_id, artifact_name
from runner.protocol import job_environment
from services.storage import get_execution_store
from services.aws_connection import get_connection, require_record_connection
from services import runner_client


def create_execution_id():
    return f"exec_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _build_env(connection):
    # Shared allowlist contract, retained for tools/tests that inspect credential scope.
    return job_environment({'identity': runner_client.identity(connection.identity), 'credentials': {
        'access_key': connection.access_key, 'secret_key': connection.secret_key, 'token': connection.token or ''}})


def run_terraform_plan(hcl_config, execution_id):
    try:
        workspace_id(execution_id)
        from runner.policy import validate_source
        validate_source(hcl_config, runner_client.identity(get_connection().identity))
        bucket = os.getenv('TF_STATE_BUCKET', '').strip()
        backend = {'bucket': bucket, 'region': os.getenv('TF_STATE_REGION', 'us-east-1')} if bucket else None
        return runner_client.operation('plan', execution_id, credentials=True, hcl=hcl_config, state_backend=backend)
    except Exception as error:
        return {'success': False, 'plan_output': str(error) if isinstance(error, ValueError) else 'Isolated planning unavailable',
                'resources_to_add': 0, 'resources_to_change': 0, 'resources_to_destroy': 0}


def run_terraform_apply(execution_id):
    from services.action_service import claimed_action
    action = claimed_action.get()
    if not action or action['execution_id'] != execution_id or get_execution_store().get(execution_id).get('status') != 'applying':
        return {'success': False, 'uncertain': False, 'apply_output': 'A claimed, reviewed runner action is required'}
    require_record_connection(execution_id)
    try:
        return runner_client.operation('apply', action['workspace_id'], credentials=True,
                                       plan_file=action['plan_file'], expected_manifest=action['manifest'])
    except Exception:
        # Dispatch may have reached the runner even if its response was lost.
        return {'success': False, 'uncertain': True, 'apply_output': 'Runner response unavailable; inspect remote state before further action'}


def plan_destroy(workspace_id_value, plan_file):
    workspace_id(workspace_id_value); artifact_name(plan_file)
    return runner_client.operation('destroy_plan', workspace_id_value, credentials=True, plan_file=plan_file)


def get_key_file(execution_id, filename):
    workspace_id(execution_id)
    if filename != 'ssh-key.pem':
        raise ValueError('Only the fixed runner-exported SSH key artifact can be downloaded')
    record = get_execution_store().get(execution_id)
    if not record.get('runner_contract') or record.get('status') != 'complete' or record.get('action_type') != 'terraform_apply':
        raise FileNotFoundError('No completed runner key export is available; recover legacy keys from a stopped backup')
    result = runner_client.operation('key', record['workspace_id'], aws_identity=record['aws_identity'])
    try:
        content = base64.b64decode(result['content_base64'], validate=True)
    except Exception:
        raise ValueError('Invalid runner key response') from None
    if len(content) > 65536:
        raise ValueError('Runner key artifact exceeds size limit')
    return content


def log_execution(entry):
    get_execution_store().append(entry)


def log_execution_update(execution_id, updates):
    get_execution_store().update(execution_id, updates)


def get_execution_history():
    return get_execution_store().history()
