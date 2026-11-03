"""Real FastAPI/FastMCP routing and lifespan, with cloud/model boundaries mocked."""

import importlib
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from services.storage import ExecutionStore




def test_both_http_entrypoints_expose_the_same_routes(environment):
    server, _, _, _ = environment
    import main
    main = importlib.reload(main)
    alternate = server.create_app()
    assert {r.path for r in main.app.routes} == {r.path for r in alternate.routes}
    for app in (main.app, alternate):
        with TestClient(app, headers={"Authorization": "Bearer test-mcp-token"}) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            assert client.get("/api/ollama/status").json() == {"state": "ready"}
            assert client.post("/tools/health_check").json()["status"] == "ok"
            assert client.get("/terraform/keys/missing/test.pem").status_code == 404
            assert "/api/ollama/pull" in client.get("/openapi.json").json()["paths"]


def test_readiness_failure_does_not_change_liveness(environment, monkeypatch):
    _, factory, store, _ = environment
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        monkeypatch.setattr(store, "check", Mock(side_effect=OSError("private path and details")))
        result = client.get("/ready")
        assert result.status_code == 503
        assert result.json()["checks"]["database"] == "unavailable"
        assert "private" not in result.text
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("dependency", ["knowledge_base", "terraform", "terraform_workdirs"])
def test_missing_required_dependency_is_not_ready(environment, monkeypatch, dependency):
    server, factory, _, _ = environment
    if dependency == "knowledge_base":
        monkeypatch.setattr(server, "_RAG_AVAILABLE", False)
    elif dependency == "terraform":
        import services.readiness as readiness
        monkeypatch.setattr(readiness.shutil, "which", lambda name: None)
    else:
        path = factory.WORKDIR_BASE
        path.write_text("a file cannot serve as a workdir")
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        result = client.get("/ready")
        assert result.status_code == 503
        assert result.json()["checks"][dependency] == "unavailable"


def test_upload_and_ollama_validation_use_shared_http_routes(environment):
    _, factory, _, knowledge_base = environment
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        response = client.post(
            "/rag/documents/upload", data={"doc_id": "test", "resource_type": "iam"},
            files={"file": ("test.txt", b"document text", "text/plain")},
        )
        assert response.status_code == 200
        assert response.json() == {"chunks_added": 1, "doc_id": "test"}
        knowledge_base.add_document.assert_called_once_with(
            doc_id="test", text="document text", metadata={"resource_type": "iam", "filename": "test.txt"},
        )
        assert client.post("/api/ollama/pull", json={"model": "unsupported"}).status_code == 400
        response = client.post(
            "/rag/documents/upload", data={"doc_id": "empty"},
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert response.status_code == 422


def test_lifespan_imports_history_and_rest_keeps_response_shape(environment):
    _, factory, store, _ = environment
    store.legacy_log.write_text('[{"execution_id":"old","status":"complete"}]')
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        response = client.post("/tools/get_execution_history_tool")
        assert response.status_code == 200
        assert response.json() == {"executions": [{"execution_id": "old", "status": "complete"}]}
    store.append({"execution_id": "new", "status": "awaiting_approval"})
    # New app and new store instance simulate a container replacement reusing
    # its persistent volume; no real Docker engine or cloud services are needed.
    recreated = ExecutionStore(store.data_dir, store.legacy_log)
    assert [row["execution_id"] for row in recreated.history()] == ["old", "new"]


def test_malformed_legacy_history_prevents_startup(environment):
    _, factory, store, _ = environment
    store.legacy_log.write_text("not json")
    with pytest.raises(ValueError):
        with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}):
            pytest.fail("startup must not silently discard history")
    assert next((store.data_dir / "backups").glob("execution_log-*.json")).read_text() == "not json"


def test_mcp_http_initialization_uses_mounted_lifespan(environment):
    _, factory, _, _ = environment
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        response = client.post("/mcp/", headers={"Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        assert response.status_code == 200
        assert '"serverInfo"' in response.text


def test_ollama_pull_stream_and_private_key_download(environment, monkeypatch):
    server, factory, _, _ = environment

    async def progress(base_url, model):
        yield '{"status":"success"}\n'

    monkeypatch.setattr(server, "stream_ollama_pull", progress)
    import services.execution_service as execution
    keydir = execution.WORKDIR_BASE / "test-execution"
    keydir.mkdir(parents=True)
    (keydir / "test.pem").write_bytes(b"test fixture, not a real key")
    with TestClient(factory.create_app(), headers={"Authorization": "Bearer test-mcp-token"}) as client:
        response = client.post("/api/ollama/pull", json={"model": server.DEFAULT_MODEL})
        assert response.status_code == 200
        assert response.text == '{"status":"success"}\n'
        response = client.get("/terraform/keys/test-execution/test.pem")
        assert response.content == b"test fixture, not a real key"
        assert response.headers["content-disposition"] == 'attachment; filename="test.pem"'


@pytest.mark.parametrize("stdio", [False, True])
def test_script_entrypoint_reuses_registered_server(environment, monkeypatch, stdio):
    server, factory, _, _ = environment
    # run_path temporarily creates __main__; preserve the module alias when done.
    monkeypatch.setitem(sys.modules, "mcp_server", server)
    monkeypatch.setattr(sys, "argv", ["mcp_server.py"] + (["--stdio"] if stdio else []))
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    from fastmcp import FastMCP
    transport = Mock()
    monkeypatch.setattr(FastMCP, "run", transport)
    import uvicorn
    http = Mock()
    monkeypatch.setattr(uvicorn, "run", http)
    result = runpy.run_path(str(Path(server.__file__)), run_name="__main__")
    if stdio:
        transport.assert_called_once_with()
        http.assert_not_called()
    else:
        transport.assert_not_called()
        http.assert_called_once()
        assert {r.path for r in http.call_args.args[0].routes} == {r.path for r in factory.create_app().routes}
    assert sys.modules["mcp_server"].mcp is result["mcp"]
