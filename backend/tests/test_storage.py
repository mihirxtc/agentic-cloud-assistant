import json
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from services.storage import ExecutionStore, prepare_chroma_storage


def test_history_survives_store_recreation(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.append({"execution_id": "one", "status": "awaiting_approval", "hcl": "test"})
    store.update("one", {"status": "complete", "resources_applied": ["test"]})
    recreated = ExecutionStore(store.data_dir, store.legacy_log)
    assert recreated.history() == [{
        "execution_id": "one", "status": "complete", "hcl": "test", "resources_applied": ["test"],
    }]


def test_legacy_import_is_backed_up_and_runs_once(tmp_path):
    legacy = tmp_path / "execution_log.json"
    entries = [{"execution_id": "old", "status": "complete", "metadata": {"port": 22}}]
    raw = json.dumps(entries, indent=2).encode()
    legacy.write_bytes(raw)
    store = ExecutionStore(tmp_path / "data", legacy)
    assert store.history() == entries
    backups = list((store.data_dir / "backups").glob("execution_log-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert backups[0].stat().st_mode & 0o777 == 0o600
    store.update("old", {"status": "rolled_back"})
    legacy.write_text(json.dumps([{"execution_id": "should-not-be-imported"}]))
    reopened = ExecutionStore(store.data_dir, legacy)
    assert reopened.history()[0]["status"] == "rolled_back"
    assert len(reopened.history()) == 1


@pytest.mark.parametrize("raw", [
    b"broken json", b"{}", b'[{}]',
    b'[{"execution_id":"duplicate"},{"execution_id":"duplicate"}]',
    b'[{"execution_id":"valid"},{"status":"invalid"}]',
])
def test_invalid_legacy_data_is_backed_up_and_import_rolls_back(tmp_path, raw):
    legacy = tmp_path / "execution_log.json"
    legacy.write_bytes(raw)
    store = ExecutionStore(tmp_path / "data", legacy)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.initialize()
    assert next((store.data_dir / "backups").glob("*.json")).read_bytes() == raw
    # Repairing the source permits a clean retry; no partial import survives.
    legacy.write_text('[{"execution_id":"repaired"}]')
    assert store.history() == [{"execution_id": "repaired"}]


def test_concurrent_store_instances_preserve_appends_and_updates(tmp_path):
    data_dir = tmp_path / "data"
    legacy = tmp_path / "execution_log.json"

    def append(index):
        ExecutionStore(data_dir, legacy).append({"execution_id": str(index)})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))
    store = ExecutionStore(data_dir, legacy)
    assert len(store.history()) == 24

    def update(index):
        ExecutionStore(data_dir, legacy).update("0", {f"field_{index}": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(24)))
    entry = next(row for row in store.history() if row["execution_id"] == "0")
    assert all(entry[f"field_{index}"] == index for index in range(24))


def test_duplicate_append_and_unknown_update_are_rejected(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.append({"execution_id": "one", "status": "complete"})
    with pytest.raises(sqlite3.IntegrityError):
        store.append({"execution_id": "one", "status": "rejected"})
    with pytest.raises(KeyError):
        store.update("missing", {"status": "complete"})
    with pytest.raises(ValueError):
        store.update("one", {"execution_id": "two"})
    assert store.history() == [{"execution_id": "one", "status": "complete"}]


def test_readiness_transaction_does_not_leave_probe_records(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.check()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT 1 FROM metadata WHERE key='readiness_probe'").fetchone() is None


def test_database_errors_are_not_reported_as_empty_history(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.initialize()
    store.path.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        store.history()


def test_future_schema_is_rejected(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.data_dir.mkdir()
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="schema"):
        store.initialize()


def test_schema_v1_upgrade_preserves_history_and_backs_up_database(tmp_path):
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    store.data_dir.mkdir()
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TABLE executions (execution_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO executions VALUES ('old', '{\"execution_id\":\"old\"}')")
        connection.execute("INSERT INTO metadata VALUES ('legacy_history_imported', 'already')")
        connection.execute("PRAGMA user_version = 1")
    assert store.history() == [{"execution_id": "old"}]
    backup = next((store.data_dir / "backups").glob("schema-v1-*.sqlite3"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT execution_id FROM executions").fetchone()[0] == "old"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_chroma_migration_preserves_source_and_backup(tmp_path):
    legacy = tmp_path / "old"
    legacy.mkdir()
    (legacy / "chroma.sqlite3").write_bytes(b"example vector data")
    target = tmp_path / "new"
    target.mkdir()  # Docker may pre-create an empty volume mount.
    data_dir = tmp_path / "data"
    prepare_chroma_storage(target, legacy, data_dir)
    assert (target / "chroma.sqlite3").read_bytes() == (legacy / "chroma.sqlite3").read_bytes()
    backup = next((data_dir / "backups").glob("chroma-*/chroma_db/chroma.sqlite3"))
    assert backup.read_bytes() == b"example vector data"
    (target / "chroma.sqlite3").write_bytes(b"new active data")
    prepare_chroma_storage(target, legacy, data_dir)
    assert (target / "chroma.sqlite3").read_bytes() == b"new active data"
    assert backup.read_bytes() == b"example vector data"


def test_failed_chroma_copy_never_activates_partial_data(tmp_path, monkeypatch):
    legacy = tmp_path / "old"
    legacy.mkdir()
    (legacy / "chroma.sqlite3").write_bytes(b"old data")
    target = tmp_path / "new"
    import services.storage as storage
    original = storage.shutil.copytree
    calls = 0

    def copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated full disk")
        return original(*args, **kwargs)

    monkeypatch.setattr(storage.shutil, "copytree", copy)
    with pytest.raises(OSError):
        prepare_chroma_storage(target, legacy, tmp_path / "data")
    assert not target.exists()
    assert (legacy / "chroma.sqlite3").read_bytes() == b"old data"
    assert not list(tmp_path.glob(".chroma-migration-*"))


def test_storage_paths_follow_configuration(tmp_path, monkeypatch):
    from services.storage import chroma_directory, data_directory
    monkeypatch.setenv("ACA_DATA_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "vectors"))
    assert data_directory() == tmp_path / "app"
    assert chroma_directory() == tmp_path / "vectors"
