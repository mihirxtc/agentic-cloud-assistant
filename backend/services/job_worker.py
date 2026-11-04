"""Run with python -m services.job_worker; shares only application data and runner API."""
import asyncio
import inspect
import signal
import threading
import uuid

from services.jobs import Jobs, active_job, operations
from services.action_identity import actor
from services.aws_connection import verify_connection, _active_connection


def run_once(queue=None, owner=None):
    queue = queue or Jobs()
    owner = owner or uuid.uuid4().hex
    queue.worker_heartbeat(owner)
    queue.recover()
    job = queue.claim(owner)
    if not job:
        return False
    stopped = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stopped.wait(10):
            try:
                queue.touch(job['job_id'], owner)
                queue.worker_heartbeat(owner)
            except Exception:
                lost.set()
                return

    def checkpoint(stage, execution_id=None):
        if lost.is_set():
            raise RuntimeError('Worker claim was lost')
        queue.touch(job['job_id'], owner, stage, execution_id)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    action_token = actor.set(job['actor'])
    job_token = active_job.set(checkpoint)
    aws_token = None
    try:
        connection = verify_connection(job['aws_identity']['region'])
        connection.require_identity(job['aws_identity'])
        aws_token = _active_connection.set(connection)
        checkpoint('Starting ' + job['operation'].replace('_', ' '))
        if job['operation'] == '_approved_action':
            from services.action_service import execute_claimed
            entry = queue.store.get(job['execution_id'])
            if entry.get('job_id') != job['job_id'] or entry['status'] != 'applying':
                raise ValueError('Approved action no longer belongs to this job')
            result = execute_claimed(entry)
        else:
            function = operations[job['operation']]
            result = function(**job['arguments'])
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        if isinstance(result, dict):
            result = {**result, 'aws_identity': connection.identity}
            if result.get('execution_id') and result.get('key_files'):
                result['key_files'] = [{**key, 'download_path': f"/terraform/keys/{result['execution_id']}/{key['name']}"} for key in result['key_files']]
        queue.finish(job['job_id'], owner, result=result)
    except Exception:
        # Persist no SDK/model exception text: it can contain credentials or request data.
        if job['operation'] == '_approved_action':
            try:
                entry = queue.store.get(job['execution_id'])
                if entry['status'] == 'applying':
                    checkpoint('Recording unavailable execution outcome')
                    queue.store.finish_action(entry['execution_id'], 'uncertain', {'error': 'Worker could not establish the execution outcome; reconciliation is required'})
                queue.finish(job['job_id'], owner, result={'status': 'uncertain', 'execution_id': entry['execution_id']})
            except Exception:
                pass  # Expired claims are recovered by the next worker, never replayed.
        else:
            queue.finish(job['job_id'], owner, error='Background operation failed. Inspect the job and create a new request when safe.')
    finally:
        if aws_token is not None:
            if job['operation'] == '_approved_action':
                from services.aws_cache import invalidate_account
                invalidate_account(connection)
            _active_connection.reset(aws_token)
        active_job.reset(job_token)
        actor.reset(action_token)
        stopped.set()
        thread.join(timeout=2)
    return True


def main():
    import mcp_server  # Registers the fixed operation allowlist and loads server configuration.
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    queue = Jobs()
    owner = uuid.uuid4().hex
    while not stop.is_set():
        if not run_once(queue, owner):
            stop.wait(1)


if __name__ == '__main__':
    main()
