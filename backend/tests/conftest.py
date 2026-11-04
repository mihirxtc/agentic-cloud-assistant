"""Offline HTTP fixture: isolated storage and no cloud/model connections."""
import hashlib
import socket
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from services.storage import ExecutionStore


@pytest.fixture
def environment(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Offline tests must not connect to external services")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("ACA_MCP_TOKEN_SHA256", hashlib.sha256(b"test-mcp-token").hexdigest())
    monkeypatch.setenv("ACA_OPERATOR_PASSWORD_HASH", "")
    monkeypatch.setenv("ACA_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("ACA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma"))
    # Supply only the knowledge-base interface. Never import/download embeddings
    # or open the developer's real Chroma collection during these tests.
    knowledge_base = Mock()
    knowledge_base.storage_path = tmp_path / "chroma"
    knowledge_base.get_document_count.return_value = 1
    knowledge_base.add_document.return_value = {"chunks_added": 1}
    kb_module = ModuleType("rag.knowledge_base")
    kb_module.knowledge_base = knowledge_base
    rag_module = ModuleType("rag.rag_service")
    rag_module.query_knowledge_base = Mock()
    monkeypatch.setitem(sys.modules, "rag.knowledge_base", kb_module)
    monkeypatch.setitem(sys.modules, "rag.rag_service", rag_module)
    import mcp_server
    monkeypatch.setattr(mcp_server, "knowledge_base", knowledge_base, raising=False)
    monkeypatch.setattr(mcp_server, "_RAG_AVAILABLE", True)
    monkeypatch.setattr(mcp_server, "probe_ollama", AsyncMock(return_value={"state": "ready"}))
    import services.storage as storage
    store = ExecutionStore(tmp_path / "data", tmp_path / "execution_log.json")
    monkeypatch.setattr(storage, "_default_store", store)
    from runner_test_support import FakeRunner
    from services import runner_client
    fake_runner = FakeRunner(tmp_path / "workdirs")
    store.test_runner = fake_runner
    monkeypatch.setattr(runner_client.RunnerClient, "request", lambda self, body: fake_runner.request(body))
    # Existing service regressions execute within worker context; queue integration
    # tests replace this with the production default to exercise submission.
    from contextvars import ContextVar
    import services.jobs as jobs
    monkeypatch.setattr(jobs, 'active_job', ContextVar('test_worker', default=lambda *_: None))
    import http_app
    return mcp_server, http_app, store, knowledge_base
