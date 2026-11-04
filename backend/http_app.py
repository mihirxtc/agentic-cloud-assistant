"""Shared HTTP app factory for Docker, local development, and MCP HTTP startup."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
import os

from docs_generator import register_mcp_docs_routes
from services.execution_service import get_key_file
from services.readiness import check_readiness
from services.storage import get_execution_store
from services.auth import AuthConfig, AuthService, AuthenticationMiddleware
from services.aws_connection import verify_connection, AWSIdentityError


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def create_app():
    import mcp_server as server

    mcp_http_app = server.mcp.http_app(path="/")
    store = get_execution_store()
    auth = AuthService(store, AuthConfig.from_env())

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        async with mcp_http_app.lifespan(app):
            yield

    app = FastAPI(
        title="Agentic Cloud Assistant — MCP API",
        description="AWS management tools exposed through MCP and documented REST wrappers.",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(AuthenticationMiddleware, auth=auth)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(auth.config.origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    app.mount("/mcp", mcp_http_app)
    register_mcp_docs_routes(app, server.mcp)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default error includes the rejected input, which can contain
        # passwords or provider credentials. Return locations/messages only.
        details = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                   for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    @app.post("/auth/login", tags=["Authentication"])
    def login(body: LoginInput, request: Request, response: Response):
        result, status = auth.login(body.username, body.password,
                                    request.client.host if request.client else "unknown",
                                    request.cookies.get(auth.config.cookie_name, ""))
        if status != 200:
            messages = {401: "Invalid credentials", 429: "Too many login attempts; retry later",
                        503: "Browser login is not configured on the server"}
            headers = {"Retry-After": "300"} if status == 429 else None
            raise HTTPException(status_code=status, detail=messages[status], headers=headers)
        response.set_cookie(auth.config.cookie_name, result["token"],
                            max_age=auth.config.session_seconds, httponly=True,
                            secure=auth.config.secure_cookie, samesite="strict", path="/")
        return {"authenticated": True, "username": result["username"],
                "csrf_token": result["csrf_token"], "expires_at": result["expires"]}

    @app.get("/auth/session", tags=["Authentication"])
    def session(request: Request):
        identity = request.state.identity
        return {"authenticated": True, "username": identity["operator"],
                "csrf_token": identity.get("csrf_token"), "expires_at": identity.get("expires")}

    @app.post("/auth/logout", tags=["Authentication"])
    def logout(request: Request, response: Response):
        identity = request.state.identity
        if identity["kind"] != "session":
            raise HTTPException(status_code=400, detail="Logout requires a browser session")
        store.delete_session(identity["token_hash"])
        response.delete_cookie(auth.config.cookie_name, path="/", httponly=True,
                               secure=auth.config.secure_cookie, samesite="strict")
        return {"authenticated": False}

    @app.get("/auth/config", tags=["Configuration"])
    def configuration_status():
        return {
            "providers": {"groq": bool(os.getenv("GROQ_API_KEY", "").strip()),
                          "anthropic": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
                          "ollama": True},
            "aws": {"credential_source": "server", "identity_verified": False,
                    "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1")},
        }

    @app.get("/auth/aws-identity", tags=["Configuration"])
    def aws_identity(region: str = ""):
        try:
            return {"aws_identity": verify_connection(region).identity}
        except AWSIdentityError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None

    @app.get("/health", tags=["Health"], summary="Process liveness")
    def health():
        return {"status": "ok", "version": "2.0.0", "mcp_endpoint": "POST /mcp"}

    @app.get("/ready", tags=["Health"], summary="Local dependency readiness")
    def ready():
        knowledge_base = getattr(server, "knowledge_base", None) if server._RAG_AVAILABLE else None
        result = check_readiness(store, knowledge_base)
        result["checks"]["authentication"] = "ok" if auth.config.configured else "unavailable"
        if not auth.config.configured:
            result["status"] = "not_ready"
        return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)

    @app.get("/ready/terraform", tags=["Health"])
    def terraform_readiness():
        from services.runner_client import readiness
        result = readiness()
        return JSONResponse(result, status_code=200 if result["ready"] else 503)

    @app.get('/ready/jobs', tags=['Health'])
    def worker_readiness():
        from services.jobs import Jobs
        ready = Jobs(store).worker_ready()
        return JSONResponse({'ready': ready}, status_code=200 if ready else 503)

    @app.get('/jobs', tags=['Jobs'])
    def list_jobs(limit: int = 50):
        from services.jobs import Jobs
        return {'jobs': Jobs(store).list(limit)}

    @app.get('/jobs/{job_id}', tags=['Jobs'])
    def get_job(job_id: str):
        from services.jobs import Jobs, public
        try:
            return public(Jobs(store).get(job_id))
        except KeyError:
            raise HTTPException(status_code=404, detail='Job does not exist') from None

    @app.post('/jobs/{job_id}/cancel', tags=['Jobs'])
    def cancel_job(job_id: str):
        from services.jobs import Jobs
        try:
            return Jobs(store).cancel(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail='Job does not exist') from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @app.get("/api/ollama/status", tags=["Ollama"])
    async def ollama_status(request: Request):
        return await server._ollama_status_endpoint(request)

    @app.post("/api/ollama/pull", tags=["Ollama"])
    async def ollama_pull(request: Request):
        return await server._ollama_pull_endpoint(request)

    @app.get(
        "/terraform/keys/{execution_id}/{filename}",
        tags=["Terraform"],
        summary="Download a .pem private key generated by a Terraform apply",
    )
    def download_key_file(execution_id: str, filename: str):
        """
        Binary file download — cannot be an MCP tool because MCP returns JSON only.
        This is the one endpoint that must remain as a real REST route.
        """
        try:
            key_bytes = get_key_file(execution_id, filename)
            return Response(
                content=key_bytes,
                media_type="application/x-pem-file",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except (FileNotFoundError, KeyError):
            raise HTTPException(status_code=404, detail="Key artifact not found")


    @app.post(
        "/rag/documents/upload",
        tags=["Knowledge Base"],
        summary="Upload a PDF or plain-text file to the ChromaDB knowledge base",
    )
    async def upload_rag_document(
        file: UploadFile = File(...),
        doc_id: str = Form(...),
        resource_type: str = Form("general"),
    ):
        """
        Multipart file upload — cannot be an MCP tool parameter because MCP is JSON-only.
        This mirrors the rag_upload_file MCP tool but accepts raw bytes via multipart/form-data.
        """
        if not server._RAG_AVAILABLE:
            raise HTTPException(status_code=503, detail="RAG knowledge base is not available.")
        try:
            content = await file.read()
            if file.filename and file.filename.lower().endswith(".pdf"):
                import io
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
                text = "\n\n".join(p.extract_text() or "" for p in pdf_reader.pages).strip()
            else:
                text = content.decode("utf-8", errors="ignore").strip()
            if not text:
                raise HTTPException(status_code=422, detail="No text could be extracted from the file.")
            result = server.knowledge_base.add_document(
                doc_id=doc_id,
                text=text,
                metadata={"resource_type": resource_type, "filename": file.filename or ""},
            )
            return {"chunks_added": result.get("chunks_added", 0), "doc_id": doc_id}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app
