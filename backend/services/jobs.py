"""Durable single-operator queue. Only registered operations can run; secrets stay in memory."""
import asyncio
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import time
import uuid

from services.action_identity import actor
from services.storage import get_execution_store

active_job = ContextVar('aca_active_job', default=None)
operations = {}
LEASE_SECONDS = 60
TERMINAL = {'complete', 'failed', 'interrupted', 'cancelled', 'reconciliation_required'}


def public(job, result=True):
    keys = ('job_id', 'operation', 'status', 'stage', 'created_at', 'updated_at', 'events',
            'aws_identity', 'execution_id', 'error', 'reconciliation')
    value = {key: job[key] for key in keys if key in job}
    if result and 'result' in job:
        value['result'] = job['result']
        if isinstance(job['result'], dict) and job['result'].get('execution_id'):
            from services.action_service import action_view
            try:
                current = get_execution_store().get(job['result']['execution_id'])
                value['result'] = {**job['result'], **action_view(current)}
            except KeyError:
                pass
        if isinstance(value['result'], dict) and value['result'].get('execution_id') and value['result'].get('key_files'):
            entry = value['result']
            entry['key_files'] = [{**key, 'download_path': f"/terraform/keys/{entry['execution_id']}/{key['name']}"} for key in entry['key_files']]
    return value


def new_job(operation, arguments, identity):
    if arguments.get('api_key'):
        raise ValueError('Background jobs use server model configuration; caller API keys are not accepted')
    arguments = {key: value for key, value in arguments.items() if key != 'api_key'}
    if len(json.dumps(arguments).encode()) > 300 * 1024:
        raise ValueError('Job input exceeds size limit')
    now = time.time()
    return {'job_id': 'job_' + uuid.uuid4().hex, 'operation': operation, 'arguments': arguments,
            'aws_identity': identity, 'actor': dict(actor.get()), 'status': 'queued', 'stage': 'Queued',
            'created_at': now, 'updated_at': now, 'events': [{'at': now, 'stage': 'Queued'}]}


class Jobs:
    def __init__(self, store=None):
        self.store = store or get_execution_store()
        self.store.initialize()

    @staticmethod
    def insert(connection, job):
        connection.execute('INSERT INTO jobs VALUES (?, ?, NULL, NULL, ?)',
                           (job['job_id'], job['status'], json.dumps(job)))

    def worker_heartbeat(self, owner):
        with self.store._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('job_worker_heartbeat', ?)", (str(time.time()),))
            connection.commit()

    def worker_ready(self):
        with self.store._connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='job_worker_heartbeat'").fetchone()
        return bool(row and 0 <= time.time() - float(row[0]) < LEASE_SECONDS)

    def submit(self, operation, arguments, identity):
        if operation not in operations:
            raise ValueError('Only registered background operations can be submitted')
        job = new_job(operation, arguments, identity)
        # An explicit operation request key provides durable submission idempotency.
        key = arguments.get('idempotency_key')
        from services.action_service import fingerprint
        digest = fingerprint({'operation': operation, 'arguments': arguments, 'identity':
                              {k: identity[k] for k in ('account_id', 'region', 'partition', 'arn')}})
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if key:
                row = connection.execute('SELECT request_hash, job_id FROM job_requests WHERE request_key = ?', ('submit:' + key,)).fetchone()
                if row:
                    if row[0] != digest:
                        raise ValueError('Job idempotency key was used for different content')
                    return public(json.loads(connection.execute('SELECT payload FROM jobs WHERE job_id = ?', (row[1],)).fetchone()[0]))
            self.insert(connection, job)
            if key:
                connection.execute('INSERT INTO job_requests VALUES (?, ?, ?)', ('submit:' + key, digest, job['job_id']))
            connection.commit()
        return public(job)

    def get(self, job_id):
        with self.store._connection() as connection:
            row = connection.execute('SELECT payload FROM jobs WHERE job_id = ?', (job_id,)).fetchone()
        if not row:
            raise KeyError('Job does not exist')
        return json.loads(row[0])

    def list(self, limit=50):
        with self.store._connection() as connection:
            rows = connection.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ?', (max(1, min(limit, 100)),)).fetchall()
        return [public(json.loads(row[0]), result=False) for row in rows]

    @staticmethod
    def save(connection, job, owner=None, lease=None):
        connection.execute('UPDATE jobs SET status=?, owner=?, lease_until=?, payload=? WHERE job_id=?',
                           (job['status'], owner, lease, json.dumps(job), job['job_id']))

    def recover(self, now=None):
        now = time.time() if now is None else now
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("SELECT payload FROM jobs WHERE status='running' AND lease_until < ?", (now,)).fetchall()
            for row in rows:
                job = json.loads(row[0])
                mutation = job['operation'] == '_approved_action'
                job.update(status='reconciliation_required' if mutation else 'interrupted',
                           stage='Interrupted; review required', updated_at=now,
                           error='Worker heartbeat expired. This job will not automatically retry.')
                execution_id = job.get('execution_id')
                if execution_id:
                    row = connection.execute('SELECT payload FROM executions WHERE execution_id=?', (execution_id,)).fetchone()
                    entry = json.loads(row[0]) if row else None
                    if entry and entry['status'] in ('complete', 'invalidated', 'rejected', 'awaiting_approval', 'plan_failed'):
                        from services.action_service import action_view
                        # The action transaction committed before the worker lost its response.
                        job.update(status='complete', stage='Recovered saved action result', result=action_view(entry), error=None)
                    elif entry and mutation and entry['status'] == 'applying':
                        entry.update(status='uncertain', error='Worker interrupted; reconcile before any further action')
                        self.store._save_action(connection, entry, 'applying')
                job['events'] = (job['events'] + [{'at': now, 'stage': job['stage']}])[-100:]
                self.save(connection, job)
                # Do not release plan/action locks: the previous process or runner may still be active.
            connection.commit()

    def claim(self, owner, now=None):
        now = time.time() if now is None else now
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT payload FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = json.loads(row[0])
            job.update(status='running', stage='Verifying AWS connection', updated_at=now)
            self.save(connection, job, owner, now + LEASE_SECONDS)
            connection.commit()
        return job

    def touch(self, job_id, owner, stage=None, execution_id=None):
        now = time.time()
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT payload FROM jobs WHERE job_id=? AND status='running' AND owner=? AND lease_until>=?",
                                     (job_id, owner, now)).fetchone()
            if not row:
                raise RuntimeError('Job lease was lost; dispatch is blocked')
            job = json.loads(row[0])
            job['updated_at'] = now
            if execution_id:
                job['execution_id'] = execution_id
            if stage and job['stage'] != stage:
                job['stage'] = stage
                job['events'] = (job['events'] + [{'at': now, 'stage': stage}])[-100:]
            self.save(connection, job, owner, now + LEASE_SECONDS)
            connection.commit()

    def finish(self, job_id, owner, result=None, error=None):
        now = time.time()
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT payload FROM jobs WHERE job_id=? AND status='running' AND owner=? AND lease_until>=?", (job_id, owner, now)).fetchone()
            if not row:
                return  # A fenced worker cannot overwrite recovery decisions.
            job = json.loads(row[0])
            action_status = (result or {}).get('status') if isinstance(result, dict) else None
            job.update(status='reconciliation_required' if action_status in ('uncertain', 'failed') and job['operation'] == '_approved_action' else 'failed' if error else 'complete',
                       stage='Finished' if not error else 'Failed', updated_at=now)
            if error:
                job['error'] = error
            else:
                job['result'] = result
                if isinstance(result, dict) and result.get('execution_id'):
                    job['execution_id'] = result['execution_id']
            job['events'] = (job['events'] + [{'at': now, 'stage': job['stage']}])[-100:]
            self.save(connection, job)
            connection.commit()

    def cancel(self, job_id):
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT payload FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError('Job does not exist')
            job = json.loads(row[0])
            if job['status'] != 'queued':
                raise ValueError('Only queued work can be cancelled; dispatched changes cannot be undone by cancellation')
            if job['operation'] == '_approved_action':
                entry = json.loads(connection.execute('SELECT payload FROM executions WHERE execution_id=?', (job['execution_id'],)).fetchone()[0])
                if entry['status'] != 'applying' or entry.get('job_id') != job_id:
                    raise ValueError('Action no longer belongs to this queued job')
                entry.update(status='rejected', error='Approved job cancelled before worker claim; no mutation was dispatched')
                self.store._save_action(connection, entry, 'applying')
                connection.execute('DELETE FROM action_locks WHERE execution_id=?', (entry['execution_id'],))
            job.update(status='cancelled', stage='Cancelled before start', updated_at=time.time())
            self.save(connection, job)
            connection.commit()
        return public(job)


def progress(stage, execution_id=None):
    current = active_job.get()
    if current is not None:
        current(stage, execution_id)


def durable(function):
    """Placed inside aws_operation: submission and worker execution both verify identity."""
    operations[function.__name__] = function
    signature = inspect.signature(function)

    def submit(args, kwargs):
        from services.aws_connection import get_connection
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return Jobs().submit(function.__name__, dict(bound.arguments), get_connection().identity)

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            if active_job.get() is not None:
                return await function(*args, **kwargs)
            return await asyncio.to_thread(submit, args, kwargs)
        return asynchronous
    @wraps(function)
    def synchronous(*args, **kwargs):
        return function(*args, **kwargs) if active_job.get() is not None else submit(args, kwargs)
    return synchronous
