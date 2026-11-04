"""Local durable storage. No AWS clients, embedding models, or network access."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import uuid

from filelock import FileLock

BACKEND_DIR = Path(__file__).resolve().parent.parent


def data_directory() -> Path:
    return Path(os.getenv("ACA_DATA_DIR", str(BACKEND_DIR / "data"))).resolve()


def chroma_directory() -> Path:
    return Path(os.getenv("CHROMA_DB_PATH", str(BACKEND_DIR / "chroma_db"))).resolve()


class ExecutionStore:
    """Transactional history with the same dictionary payloads as the old log."""

    def __init__(self, data_dir: Path, legacy_log: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "aca.sqlite3"
        self.legacy_log = Path(legacy_log)
        self._initialized = False
        self._init_lock = threading.Lock()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self._init_lock:
            if self._initialized:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._connection() as connection:
                os.chmod(self.path, 0o600)
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2, 3, 4, 5):
                    raise RuntimeError("Unsupported application database schema")
                if version in (1, 2, 3, 4):
                    backups = self.data_dir / "backups"
                    backups.mkdir(exist_ok=True, mode=0o700)
                    backup = backups / f"schema-v{version}-{uuid.uuid4().hex}.sqlite3"
                    with closing(sqlite3.connect(backup)) as destination:
                        os.chmod(backup, 0o600)
                        connection.backup(destination)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS executions "
                    "(execution_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS metadata "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS sessions "
                    "(token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, "
                    "operator TEXT NOT NULL, config_tag TEXT NOT NULL, expires REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS login_attempts "
                    "(source TEXT PRIMARY KEY, window_start REAL NOT NULL, attempts INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS scans (scan_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                connection.execute("CREATE TABLE IF NOT EXISTS action_requests (request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, execution_id TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS action_locks (lock_key TEXT PRIMARY KEY, execution_id TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS action_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, owner TEXT, lease_until REAL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS job_requests (request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, job_id TEXT NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, lease_until)")
                if version in (1, 2, 3, 4):
                    import time
                    for (payload,) in connection.execute('SELECT payload FROM executions').fetchall():
                        entry = json.loads(payload)
                        if entry.get('status') not in ('planning', 'applying', 'uncertain', 'failed') or entry.get('job_id'):
                            continue
                        job_id = 'job_' + uuid.uuid4().hex
                        mutation = entry['status'] != 'planning'
                        job = {'job_id': job_id, 'operation': '_legacy_action', 'status': 'reconciliation_required' if mutation else 'interrupted',
                               'stage': 'Legacy operation requires review', 'created_at': time.time(), 'updated_at': time.time(),
                               'execution_id': entry['execution_id'], 'aws_identity': entry.get('aws_identity'), 'events': [],
                               'error': 'Imported history is never automatically dispatched; inspect original worker and AWS state.'}
                        connection.execute('INSERT INTO jobs VALUES (?, ?, NULL, NULL, ?)', (job_id, job['status'], json.dumps(job)))
                        entry['job_id'] = job_id
                        if entry['status'] == 'applying':
                            entry['status'] = 'uncertain'
                        connection.execute('UPDATE executions SET payload=? WHERE execution_id=?', (json.dumps(entry), entry['execution_id']))
                connection.execute("PRAGMA user_version = 5")
                if not connection.execute(
                    "SELECT 1 FROM metadata WHERE key = 'legacy_history_imported'"
                ).fetchone():
                    self._import_history(connection)
                    connection.execute(
                        "INSERT INTO metadata VALUES ('legacy_history_imported', ?)",
                        (datetime.now(timezone.utc).isoformat(),),
                    )
                connection.commit()
            self._initialized = True

    def _import_history(self, connection):
        if not self.legacy_log.exists():
            return
        raw = self.legacy_log.read_bytes()
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(exist_ok=True, mode=0o700)
        digest = hashlib.sha256(raw).hexdigest()
        backup = backup_dir / f"execution_log-{digest}.json"
        # Write the backup before parsing or importing. A malformed legacy file
        # must fail loudly, never become an apparently empty execution history.
        try:
            with backup.open("xb") as output:
                os.chmod(backup, 0o600)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            if backup.read_bytes() != raw:
                raise RuntimeError("Legacy history backup verification failed")
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise ValueError("Legacy execution history must be a list")
        for entry in entries:
            self._validate_entry(entry)
            connection.execute(
                "INSERT INTO executions VALUES (?, ?)",
                (entry["execution_id"], json.dumps(entry, default=str)),
            )

    @staticmethod
    def _validate_entry(entry):
        if not isinstance(entry, dict) or not isinstance(entry.get("execution_id"), str) or not entry["execution_id"].strip():
            raise ValueError("Execution record requires a nonempty execution_id")

    def append(self, entry: dict):
        self._validate_entry(entry)
        self.initialize()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO executions VALUES (?, ?)",
                (entry["execution_id"], json.dumps(entry, default=str)),
            )
            connection.commit()

    def update(self, execution_id: str, updates: dict):
        if "execution_id" in updates and updates["execution_id"] != execution_id:
            raise ValueError("Execution IDs cannot be changed")
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Execution does not exist")
            entry = json.loads(row[0])
            if entry.get("approval_digest") and set(updates) - {"resolved", "resolved_at"}:
                raise ValueError("Reviewed actions must use transactional action transitions")
            entry.update(updates)
            connection.execute(
                "UPDATE executions SET payload = ? WHERE execution_id = ?",
                (json.dumps(entry, default=str), execution_id),
            )
            connection.commit()

    @staticmethod
    def _action_event(connection, entry, previous):
        from services.action_identity import actor
        event = {"from": previous, "to": entry["status"], "at": datetime.now(timezone.utc).isoformat(),
                 "actor": actor.get(), "approval_digest": entry.get("approval_digest")}
        connection.execute("INSERT INTO action_events (execution_id, payload) VALUES (?, ?)",
                           (entry["execution_id"], json.dumps(event)))

    @staticmethod
    def _save_action(connection, entry, previous):
        connection.execute("UPDATE executions SET payload = ? WHERE execution_id = ?",
                           (json.dumps(entry), entry["execution_id"]))
        ExecutionStore._action_event(connection, entry, previous)

    @staticmethod
    def _lock_action(connection, entry):
        key = entry["lock_key"]
        current = connection.execute("SELECT execution_id FROM action_locks WHERE lock_key = ?", (key,)).fetchone()
        if current and current[0] != entry["execution_id"]:
            raise ValueError("Another pending or uncertain action owns this target; review its history first")
        connection.execute("INSERT OR IGNORE INTO action_locks VALUES (?, ?)", (key, entry["execution_id"]))

    def create_action(self, entry, request_key="", request_hash="", reserve=False):
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_key:
                old = connection.execute("SELECT request_hash, execution_id FROM action_requests WHERE request_key = ?", (request_key,)).fetchone()
                if old:
                    if old[0] != request_hash:
                        raise ValueError("Idempotency key was already used for a different request")
                    row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (old[1],)).fetchone()
                    return json.loads(row[0]), False
            if reserve:
                self._lock_action(connection, entry)
            connection.execute("INSERT INTO executions VALUES (?, ?)", (entry["execution_id"], json.dumps(entry)))
            if request_key:
                connection.execute("INSERT INTO action_requests VALUES (?, ?, ?)", (request_key, request_hash, entry["execution_id"]))
            self._action_event(connection, entry, None)
            connection.commit()
        return entry, True

    def prepare_action(self, execution_id, updates):
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
            if not row:
                raise KeyError("Execution does not exist")
            entry = json.loads(row[0])
            if entry["status"] != "planning":
                raise ValueError("Only a planning action can be prepared")
            entry.update(updates)
            self._save_action(connection, entry, "planning")
            if entry["status"] == "plan_failed":
                connection.execute("DELETE FROM action_locks WHERE execution_id = ?", (execution_id,))
            connection.commit()
        return entry

    def claim_action(self, execution_id, approved, digest, request_key, request_hash, job=None):
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT request_hash, execution_id FROM action_requests WHERE request_key = ?", (request_key,)).fetchone()
            if old:
                if old != (request_hash, execution_id):
                    raise ValueError("Idempotency key was already used for a different request")
                row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
                return json.loads(row[0]), False
            row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
            if not row:
                raise KeyError("Execution does not exist")
            entry = json.loads(row[0])
            if entry.get("status") != "awaiting_approval":
                raise ValueError("Action is not awaiting approval; do not retry mutations automatically")
            if approved and (not entry.get("approval_digest") or entry["approval_digest"] != digest):
                raise ValueError("Approval must match the reviewed digest; legacy actions require a new proposal")
            if approved:
                self._lock_action(connection, entry)
                identity = entry['aws_identity']
                connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                                   (f"cache_revision:{identity['partition']}:{identity['account_id']}", uuid.uuid4().hex))
            if job is not None:
                from services.jobs import Jobs
                job['execution_id'] = execution_id
                Jobs.insert(connection, job)
                entry['job_id'] = job['job_id']
            entry.update(status="applying" if approved else "rejected", decision_key=request_key,
                         approved_at=datetime.now(timezone.utc).isoformat() if approved else None)
            connection.execute("INSERT INTO action_requests VALUES (?, ?, ?)", (request_key, request_hash, execution_id))
            self._save_action(connection, entry, "awaiting_approval")
            if not approved:
                connection.execute("DELETE FROM action_locks WHERE execution_id = ?", (execution_id,))
            connection.commit()
        return entry, True

    def finish_action(self, execution_id, status, updates):
        if status not in ("complete", "failed", "uncertain", "invalidated"):
            raise ValueError("Invalid final action state")
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
            entry = json.loads(row[0])
            if entry["status"] != "applying":
                raise ValueError("Action was not claimed")
            entry.update(updates, status=status, finished_at=datetime.now(timezone.utc).isoformat())
            self._save_action(connection, entry, "applying")
            # Failed remote mutations may have partial effects: keep the target locked.
            if status in ("complete", "invalidated"):
                connection.execute("DELETE FROM action_locks WHERE execution_id = ?", (execution_id,))
            connection.commit()
        return entry

    def action_events(self, execution_id):
        self.initialize()
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload FROM action_events WHERE execution_id = ? ORDER BY sequence", (execution_id,))]

    def cache_revision(self, partition, account_id):
        self.initialize()
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (f"cache_revision:{partition}:{account_id}",)).fetchone()
        return row[0] if row else ''

    def invalidate_cache(self, partition, account_id):
        self.initialize()
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (f"cache_revision:{partition}:{account_id}", uuid.uuid4().hex))
            connection.commit()

    def history(self) -> list:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM executions ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, execution_id):
        self.initialize()
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
        if row is None:
            raise KeyError("Execution does not exist")
        return json.loads(row[0])

    def record_scan(self, identity, services):
        """Retain identity/coverage evidence, not raw inventory or credentials."""
        self.initialize()
        scan_id = "scan_" + uuid.uuid4().hex
        from services.scan_coverage import scan_coverage
        record = {"scan_id": scan_id, "aws_identity": identity,
                  "coverage": scan_coverage(services),
                  "service_statuses": {name: data.get("status", "error") for name, data in services.items()}}
        with self._connection() as connection:
            connection.execute("INSERT INTO scans VALUES (?, ?)", (scan_id, json.dumps(record)))
            connection.commit()
        return scan_id

    def get_scan(self, scan_id):
        self.initialize()
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
        if row is None:
            raise KeyError("Scan does not exist")
        return json.loads(row[0])

    def check(self):
        """Exercise a real write transaction without retaining probe data."""
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('readiness_probe', 'ok')")
            connection.rollback()

    def create_session(self, token_hash, csrf_token, operator, config_tag, expires, now, previous_hash=None):
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            if previous_hash:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (previous_hash,))
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                               (token_hash, csrf_token, operator, config_tag, expires))
            connection.commit()

    def session(self, token_hash, config_tag, now):
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT operator, csrf_token, expires FROM sessions "
                "WHERE token_hash = ? AND config_tag = ? AND expires > ?",
                (token_hash, config_tag, now),
            ).fetchone()
        return {"operator": row[0], "csrf_token": row[1], "expires": row[2]} if row else None

    def delete_session(self, token_hash):
        self.initialize()
        with self._connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            connection.commit()

    def allow_login_attempt(self, source, now):
        """Atomic rate limits: 10 attempts/source and 60 total per five minutes."""
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM login_attempts WHERE window_start <= ?", (now - 300,))
            for key, limit in ((source, 10), ("global", 60)):
                row = connection.execute("SELECT attempts FROM login_attempts WHERE source = ?", (key,)).fetchone()
                if row and row[0] >= limit:
                    connection.rollback()
                    return False
            for key in (source, "global"):
                connection.execute(
                    "INSERT INTO login_attempts VALUES (?, ?, 1) "
                    "ON CONFLICT(source) DO UPDATE SET attempts = attempts + 1", (key, now),
                )
            connection.commit()
        return True

    def clear_login_attempts(self, source):
        self.initialize()
        with self._connection() as connection:
            connection.execute("DELETE FROM login_attempts WHERE source = ?", (source,))
            connection.commit()


_default_store = None
_store_lock = threading.Lock()


def get_execution_store() -> ExecutionStore:
    global _default_store
    with _store_lock:
        if _default_store is None:
            legacy = Path(os.getenv("ACA_LEGACY_EXECUTION_LOG", str(BACKEND_DIR / "execution_log.json")))
            _default_store = ExecutionStore(data_directory(), legacy)
    return _default_store


def prepare_chroma_storage(target=None, legacy=None, data_dir=None) -> Path:
    """Copy legacy Chroma storage before opening it; never overwrite active data.

    Run during startup with old instances stopped. Backups remain untouched by
    Chroma. Staging + rename prevents a failed copy from becoming active storage.
    """
    target = Path(target) if target is not None else chroma_directory()
    legacy = Path(legacy) if legacy is not None else Path(
        os.getenv("ACA_LEGACY_CHROMA_PATH", str(BACKEND_DIR.parent / "chroma_db"))
    )
    data_dir = Path(data_dir) if data_dir is not None else data_directory()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(str(data_dir / "chroma-migration.lock"), timeout=60):
        if target.resolve() == legacy.resolve() or not legacy.is_dir() or not any(legacy.iterdir()):
            target.mkdir(parents=True, exist_ok=True)
            return target
        if target.exists() and any(target.iterdir()):
            # An explicitly populated destination is authoritative. The old
            # source remains intact; never merge separate vector databases.
            return target
        backups = data_dir / "backups"
        backups.mkdir(exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(prefix="chroma-", dir=backups))
        os.chmod(backup, 0o700)
        shutil.copytree(legacy, backup / "chroma_db")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".chroma-migration-", dir=target.parent))
        try:
            shutil.copytree(backup / "chroma_db", staging, dirs_exist_ok=True)
            if target.exists():
                target.rmdir()  # Only an empty directory can be replaced.
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target
