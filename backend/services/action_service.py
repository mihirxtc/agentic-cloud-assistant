"""Review-bound proposals and a single claimed execution path for mutations."""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import re

from services.aws_connection import get_connection, require_record_connection, require_scan_connection
from services.storage import get_execution_store
from services.security_analyzer import port_exposed

claimed_action = ContextVar('aca_claimed_action', default=None)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identity():
    value = get_connection().identity
    return {key: value[key] for key in ('account_id', 'region', 'partition', 'arn')}


def _key(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value):
        raise ValueError('Supply an idempotency key of 8–128 letters, digits, hyphens or underscores')
    return prefix + value


def action_view(entry):
    return {key: value for key, value in entry.items() if key not in ('manifest', 'permissions', 'decision_key', 'lock_key')}


def manifest(entry):
    from services import runner_client
    if entry.get('runner_contract') != 1:
        raise ValueError('Legacy plan has no isolated-runner evidence; create a new proposal')
    result = runner_client.operation('manifest', entry['workspace_id'], plan_file=entry['plan_file'])
    from runner.protocol import validate_manifest
    return validate_manifest(result.get('manifest'), entry['plan_file'])



def seal(entry):
    content = {key: entry.get(key) for key in ('action_type', 'workspace_id', 'plan_file', 'manifest',
                                               'permissions', 'sg_id', 'port', 'aws_identity', 'plan_output', 'runner_contract')}
    return fingerprint(content)


def _begin(action_type, lock_key, request, idempotency_key='', reserve=False, **fields):
    from services.execution_service import create_execution_id
    entry = {'execution_id': fields.pop('execution_id', None) or create_execution_id(), 'status': 'planning', 'action_type': action_type,
             'lock_key': lock_key, 'aws_identity': get_connection().identity,
             'timestamp': datetime.now(timezone.utc).isoformat(), **fields}
    result = get_execution_store().create_action(entry, _key(idempotency_key, 'proposal:') if idempotency_key else '',
                                               fingerprint({'action': action_type, 'identity': _identity(), 'request': request}), reserve)
    from services.jobs import progress
    progress('Preparing action', result[0]['execution_id'])
    return result


def _prepare(entry, result):
    store = get_execution_store()
    updates = {key: value for key, value in result.items() if key != 'success'}
    if result.get('success'):
        try:
            updated = {**entry, **updates}
            if entry['action_type'] != 'sg_revoke':
                updated['manifest'] = manifest(updated)
            updated['approval_digest'] = seal(updated)
            updates.update(manifest=updated.get('manifest'), approval_digest=updated['approval_digest'], status='awaiting_approval')
        except Exception:
            updates.update(status='plan_failed', error='Saved plan could not be verified; no approval is available')
    else:
        updates.update(status='plan_failed')
    from services.jobs import progress
    progress('Saving reviewable proposal', entry['execution_id'])
    return action_view(store.prepare_action(entry['execution_id'], updates))


def propose_terraform(hcl, description='', idempotency_key=''):
    from services import execution_service as execution
    # Each normal proposal creates its own workspace.
    workspace = execution.create_execution_id()
    entry, created = _begin('terraform_apply', 'tf:' + workspace, {'hcl': hcl, 'description': description},
                             idempotency_key, reserve=True, execution_id=workspace, workspace_id=workspace, plan_file='tfplan', description=description, runner_contract=1)
    if not created:
        return action_view(entry)
    result = execution.run_terraform_plan(hcl, workspace)
    return _prepare(entry, result)


def _permissions(sg_id, port):
    client = get_connection().client('ec2', region_name=get_connection().region)
    response = client.describe_security_groups(GroupIds=[sg_id])
    if len(response.get('SecurityGroups', [])) != 1:
        raise ValueError('Security group was not found')
    rules = []
    for permission in response['SecurityGroups'][0].get('IpPermissions', []):
        if not port_exposed({'protocol': permission.get('IpProtocol'), 'port': permission.get('FromPort'), 'to_port': permission.get('ToPort')}, port):
            continue
        entry = {'IpProtocol': str(permission['IpProtocol'])}
        if entry['IpProtocol'] != '-1':
            entry.update(FromPort=permission['FromPort'], ToPort=permission['ToPort'])
        for key, cidr, world in (('IpRanges', 'CidrIp', '0.0.0.0/0'), ('Ipv6Ranges', 'CidrIpv6', '::/0')):
            sources = [{cidr: item[cidr]} for item in permission.get(key, []) if item.get(cidr) == world]
            if sources:
                entry[key] = sorted(sources, key=lambda item: item[cidr])
        if 'IpRanges' in entry or 'Ipv6Ranges' in entry:
            rules.append(entry)
    return sorted(rules, key=lambda item: json.dumps(item, sort_keys=True))


def propose_ingress(sg_id, port, scan_id, idempotency_key=''):
    require_scan_connection(scan_id, get_connection().region)
    if port not in (22, 3389, -1):
        raise ValueError('Only SSH, RDP and all-protocol remediation is supported')
    entry, created = _begin('sg_revoke', 'sg:' + fingerprint([_identity()['account_id'], _identity()['region'], sg_id]),
                             {'sg_id': sg_id, 'port': port, 'scan_id': scan_id}, idempotency_key,
                             sg_id=sg_id, port=port, scan_id=scan_id, description='Review public ingress removal')
    if not created:
        return action_view(entry)
    try:
        permissions = _permissions(sg_id, port)
        if not permissions:
            raise ValueError('No matching public rules remain; rescan before proposing changes')
        return _prepare(entry, {'success': True, 'permissions': permissions,
                                'plan_output': json.dumps({'action': 'RevokeSecurityGroupIngress', 'group_id': sg_id,
                                                           'permissions': permissions}, indent=2),
                                'resources_to_add': 0, 'resources_to_change': 1, 'resources_to_destroy': 0})
    except Exception:
        return _prepare(entry, {'success': False, 'error': 'Ingress inspection failed or no matching public rule remains; rescan before retrying'})


def propose_destroy(execution_id, idempotency_key=''):
    from services import execution_service as execution
    source = get_execution_store().get(execution_id)
    require_record_connection(execution_id)
    if source.get('status') != 'complete' or source.get('action_type') != 'terraform_apply' or source.get('runner_contract') != 1:
        raise ValueError('Only a completed reviewed Terraform apply can have a destroy proposal')
    workspace = source['workspace_id']
    entry, created = _begin('terraform_destroy', 'tf:' + workspace, {'source': execution_id}, idempotency_key,
                             reserve=True, workspace_id=workspace, source_execution_id=execution_id,
                             description='Separately reviewed destroy plan', runner_contract=1)
    if not created:
        return action_view(entry)
    entry['plan_file'] = f"destroy_{entry['execution_id']}.tfplan"
    try:
        result = execution.plan_destroy(workspace, entry['plan_file'])
    except Exception:
        result = {'success': False, 'error': 'Destroy planning failed; no resources were destroyed'}
    return _prepare(entry, {**result, 'plan_file': entry['plan_file']})


def decide(execution_id, approved, approval_digest='', idempotency_key=''):
    store = get_execution_store()
    if not isinstance(approved, bool):
        raise ValueError('Approval decision must be true or false')
    # Rejection is local, including legacy proposals, and cannot mutate infrastructure.
    key = _key(idempotency_key or ('reject_' + execution_id if not approved else ''), 'decision:')
    request = fingerprint({'execution_id': execution_id, 'approved': approved, 'digest': approval_digest})
    from services.jobs import active_job, new_job, Jobs, public
    job = new_job('_approved_action', {}, get_connection().identity) if approved and active_job.get() is None else None
    entry, claimed = store.claim_action(execution_id, approved, approval_digest, key, request, job=job)
    if approved and entry.get('job_id') and active_job.get() is None:
        return public(Jobs(store).get(entry['job_id']))
    if not claimed or not approved:
        return action_view(entry)
    return execute_claimed(entry)


def execute_claimed(entry):
    """Worker-only execution of an already transactionally approved action."""
    from services import execution_service as execution
    from services.jobs import progress
    execution_id = entry['execution_id']
    store = get_execution_store()
    progress('Verifying reviewed action', execution_id)
    try:
        require_record_connection(execution_id)
        if seal(entry) != entry['approval_digest']:
            raise ValueError('Reviewed action metadata changed')
        if entry['action_type'] == 'sg_revoke':
            if _permissions(entry['sg_id'], entry['port']) != entry['permissions']:
                raise ValueError('Ingress rules changed since review; create a new proposal')
        elif manifest(entry) != entry['manifest']:
            raise ValueError('Saved plan or configuration changed; create a new proposal')
    except Exception as error:
        return action_view(store.finish_action(execution_id, 'invalidated', {'error': str(error) if isinstance(error, ValueError) else 'Action verification unavailable; create a new proposal', 'apply_output': 'Action invalidated before mutation; create a new proposal'}))
    token = claimed_action.set(entry)
    try:
        if entry['action_type'] == 'sg_revoke':
            # Submit the reviewed permissions only, never newly-discovered sources.
            client = get_connection().client('ec2', region_name=get_connection().region)
            progress('Dispatching reviewed ingress removal', execution_id)
            client.revoke_security_group_ingress(GroupId=entry['sg_id'], IpPermissions=entry['permissions'])
            result = {'success': True, 'apply_output': 'Reviewed ingress rules revoked', 'resources_applied': [entry['sg_id']]}
        else:
            progress('Applying reviewed Terraform plan', execution_id)
            result = execution.run_terraform_apply(execution_id)
        status = 'complete' if result.get('success') else 'uncertain' if result.get('uncertain') else 'failed'
        progress('Saving execution outcome', execution_id)
        return action_view(store.finish_action(execution_id, status, {key: value for key, value in result.items() if key != 'success'}))
    except BaseException as error:
        # A lost response/timeout after dispatch cannot establish whether AWS changed.
        uncertain = store.finish_action(execution_id, 'uncertain', {'error': 'Mutation outcome is uncertain. Inspect state before any further action.',
                                                       'apply_output': 'Outcome uncertain; automatic retry is blocked'})
        if isinstance(error, Exception):
            return action_view(uncertain)
        raise
    finally:
        claimed_action.reset(token)
