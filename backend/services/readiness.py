"""API readiness is local; isolated-runner availability is checked separately."""
import tempfile
from pathlib import Path


def _writable_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as probe:
        probe.write(b'ready'); probe.flush()


def check_readiness(store, knowledge_base):
    checks = {}
    try:
        store.check(); checks['database'] = 'ok'
    except Exception:
        checks['database'] = 'unavailable'
    try:
        if knowledge_base is None:
            raise RuntimeError('Knowledge base unavailable')
        _writable_directory(knowledge_base.storage_path)
        knowledge_base.get_document_count()
        checks['knowledge_base'] = 'ok'
    except Exception:
        checks['knowledge_base'] = 'unavailable'
    return {'status': 'ready' if all(value == 'ok' for value in checks.values()) else 'not_ready',
            'checks': checks, 'terraform_execution': 'isolated-runner; query authenticated /ready/terraform'}
