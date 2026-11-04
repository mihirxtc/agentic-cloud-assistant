"""Read-only outcome inspection and explicit, evidence-bound completion recovery."""
import json
import time

from services.action_service import fingerprint, action_view
from services.aws_connection import require_record_connection
from services.jobs import Jobs
from services.storage import get_execution_store


def inspect_action(execution_id):
    store = get_execution_store()
    entry = store.get(execution_id)
    connection = require_record_connection(execution_id)
    if entry.get('status') not in ('uncertain', 'failed', 'applying'):
        raise ValueError('Only interrupted or unresolved mutations need reconciliation')
    evidence = {'execution_id': execution_id, 'aws_identity': entry['aws_identity'],
                'approval_digest': entry.get('approval_digest'), 'recoverable': False}
    try:
        if entry.get('action_type') == 'sg_revoke':
            current = connection.client('ec2', region_name=connection.region).describe_security_groups(GroupIds=[entry['sg_id']])
            # This is evidence for operator review, not proof that an old SDK call has stopped.
            evidence.update(current_permissions=current['SecurityGroups'][0]['IpPermissions'],
                            reviewed_permissions=entry['permissions'],
                            message='Inspect these permissions and stop the original worker before offline recovery. An absent rule alone cannot establish the original call has terminated; the lock is retained.')
        else:
            from services import runner_client
            outcome = runner_client.operation('outcome', entry['workspace_id'], aws_identity=entry['aws_identity'], plan_file=entry['plan_file'])
            value = outcome.get('outcome') or {}
            complete = outcome.get('dispatched') is True and value.get('status') == 'complete' and value.get('result', {}).get('success') is True
            evidence.update(runner=outcome, recoverable=complete,
                            message='Runner has a durable successful outcome. Review and confirm to restore completion.' if complete else
                            'No durable successful outcome. Keep the target locked; inspect the runner and AWS state. Do not replay the saved plan.')
    except Exception:
        evidence['message'] = 'Outcome evidence unavailable or runner still active. Keep the target locked and inspect deployment state.'
    evidence['evidence_digest'] = fingerprint(evidence)
    return evidence


def reconcile(execution_id, evidence_digest):
    evidence = inspect_action(execution_id)
    if not evidence_digest or evidence['evidence_digest'] != evidence_digest:
        raise ValueError('Evidence changed; inspect and review it again')
    if not evidence['recoverable']:
        raise ValueError('Evidence cannot establish successful completion; target remains locked')
    store = get_execution_store()
    with store._connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        entry = json.loads(connection.execute('SELECT payload FROM executions WHERE execution_id=?', (execution_id,)).fetchone()[0])
        if entry['status'] not in ('uncertain', 'failed', 'applying'):
            raise ValueError('Action was already reconciled or no longer needs recovery')
        job_id = entry.get('job_id')
        job = None
        if job_id:
            job = json.loads(connection.execute('SELECT payload FROM jobs WHERE job_id=?', (job_id,)).fetchone()[0])
            if job['status'] in ('queued', 'running'):
                raise ValueError('Execution job is still active; wait for completion or expired-claim recovery')
        old_status = entry['status']
        result = {k: v for k, v in evidence['runner']['outcome']['result'].items() if k not in ('success', 'owned')}
        entry.update(result, status='complete', error=None, reconciled_at=time.time(), reconciliation_digest=evidence_digest)
        store._save_action(connection, entry, old_status)
        connection.execute('DELETE FROM action_locks WHERE execution_id=?', (execution_id,))
        if job:
            job.update(status='complete', stage='Completion reconciled', updated_at=time.time(),
                       result=action_view(entry), reconciliation={'digest': evidence_digest})
            Jobs.save(connection, job)
        connection.commit()
    return action_view(entry)
