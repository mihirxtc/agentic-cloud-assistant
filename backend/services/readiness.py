"""Readiness probes use only local storage and installed executables."""

import shutil
import tempfile
from pathlib import Path


def _writable_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as probe:
        probe.write(b"ready")
        probe.flush()


def check_readiness(store, knowledge_base, workdir, plugin_cache) -> dict:
    checks = {}
    for name, probe in (
        ("database", store.check),
        ("terraform_workdirs", lambda: _writable_directory(workdir)),
        ("terraform_plugin_cache", lambda: _writable_directory(plugin_cache)),
    ):
        try:
            probe()
            checks[name] = "ok"
        except Exception:
            checks[name] = "unavailable"
    checks["terraform"] = "ok" if shutil.which("terraform") else "unavailable"
    try:
        if knowledge_base is None:
            raise RuntimeError("Knowledge base unavailable")
        _writable_directory(knowledge_base.storage_path)
        knowledge_base.get_document_count()
        checks["knowledge_base"] = "ok"
    except Exception:
        checks["knowledge_base"] = "unavailable"
    return {
        "status": "ready" if all(status == "ok" for status in checks.values()) else "not_ready",
        "checks": checks,
    }
