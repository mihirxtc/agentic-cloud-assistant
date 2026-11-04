# MCP server for the Agentic Cloud Assistant — exposes all capabilities as tools over POST /mcp.

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
import httpx
import uvicorn

from services.aws_scanner import (
    scan_ec2,
    scan_iam,
    scan_s3,
    scan_security_groups,
    scan_vpc,
    scan_existing_infra_for_context,
    scan_sg_usage,
)
from services.cost_analyzer import (
    detect_cost_anomaly,
    get_cost_by_service,
    get_current_month_cost,
    get_monthly_trend,
)
from services.execution_service import (
    get_execution_history,
    log_execution_update,
)
from services.llm_service import (
    chat_with_anthropic,
    chat_with_groq,
    chat_with_ollama,
    prompt_llm,
)
from ollama_catalog import MODELS, DEFAULT_MODEL
from services.ollama_service import probe_ollama, stream_ollama_pull
from services.scan_coverage import assessment, scan_coverage
from services.terraform_service import (
    generate_terraform,
    handle_summarise_plan,
    validate_terraform_syntax,
)

mcp = FastMCP("agentic-cloud-assistant")

from services.aws_connection import aws_operation, get_connection, require_record_connection, require_scan_connection
from services.aws_cache import cached_context
from services.storage import get_execution_store
from services.action_service import propose_terraform, propose_ingress, propose_destroy, decide

_SCAN_SERVICES = ("ec2", "s3", "iam", "security_groups", "vpc")


def _collect_scan(region=""):
    connection = get_connection(region)
    started_at = datetime.now(timezone.utc).isoformat()
    services = {
        "ec2": scan_ec2(region=connection.region), "s3": scan_s3(), "iam": scan_iam(),
        "security_groups": scan_security_groups(region=connection.region), "vpc": scan_vpc(region=connection.region),
    }
    scan_id = get_execution_store().record_scan(connection.identity, services)
    coverage = scan_coverage(services)
    return {**services, "scan_id": scan_id, "aws_identity": connection.identity,
            "status": coverage["status"], "coverage": coverage, "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat()}


def _resolve_key(model: str, override: str) -> str:
    """Return the API key / URL to use: explicit override → env var fallback."""
    key = (override or "").strip()
    if key:
        # Reject non-URL strings being used as an Ollama base URL
        if model == "ollama" and not key.startswith(("http://", "https://")):
            return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return key
    if model == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    if model == "ollama":
        return os.getenv("OLLAMA_BASE_URL", "")
    return os.getenv("GROQ_API_KEY", "")


@mcp.tool()
def health_check() -> dict:
    """Returns server status. Used to verify MCP connection."""
    return {"status": "ok", "server": "agentic-cloud-assistant"}


@mcp.tool()
@aws_operation('region')
def full_aws_scan(region: str = "", expected_account_id: str = "") -> dict:
    """Run all five AWS service scans and return combined infrastructure data."""
    return _collect_scan(region)


@mcp.tool()
@aws_operation('region')
def aws_connection_identity(region: str = "") -> dict:
    """Verify the current server AWS account and selected region through STS."""
    return {"aws_identity": get_connection().identity}


@mcp.tool()
@aws_operation('region')
def scan_ec2_instances(region: str = "", expected_account_id: str = "") -> dict:
    """Scan all EC2 instances in the specified AWS region."""
    return scan_ec2(region=region)


@mcp.tool()
@aws_operation('region')
def scan_s3_buckets(region: str = "", expected_account_id: str = "") -> dict:
    """Scan all S3 buckets and check public-access status."""
    return scan_s3()


@mcp.tool()
@aws_operation('region')
def scan_iam_users(region: str = "", expected_account_id: str = "") -> dict:
    """Scan all IAM users and check MFA status and last-login date."""
    return scan_iam()


@mcp.tool()
@aws_operation('region')
def scan_security_groups_detail(region: str = "", expected_account_id: str = "") -> dict:
    """Scan all EC2 security groups and flag dangerous internet-facing rules."""
    return scan_security_groups(region=region)


@mcp.tool()
@aws_operation('region')
def scan_vpc_detail(region: str = "", expected_account_id: str = "") -> dict:
    """Scan all VPCs including subnet counts."""
    return scan_vpc(region=region)


@mcp.tool()
@aws_operation('region')
def scan_sg_usage_tool(
    region: str = "",
    expected_account_id: str = "",
) -> dict:
    """Map every security group to the resources currently attached to it."""

    return scan_sg_usage(region=region)


@mcp.tool()
def analyse_security_findings(scan_data: dict) -> dict:
    """Run 7 built-in security rules against raw AWS scan data."""
    return assessment(scan_data)


@mcp.tool()
@aws_operation('region')
async def run_security_analysis_with_summary(
    region: str = "",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
    expected_account_id: str = "",
) -> dict:
    """Scan AWS, run security rules, and generate an LLM plain-English summary."""
    scan_data = _collect_scan(region)

    result = assessment(scan_data)
    # Coverage and no-finding wording are deterministic, not delegated to a model.
    if result["status"] == "complete" and result["findings"]:
        summary_prompt = (
            "Summarise these built-in AWS security findings. Stay within this limited "
            "scope; do not claim the account is secure or all resources were audited.\n"
            + json.dumps(result["findings"])
        )
        try:
            result["llm_summary"] = await prompt_llm(summary_prompt, model, _resolve_key(model, api_key), model_name=ollama_model_name)
        except Exception:
            result["summary_error"] = "Model summary unavailable; confirmed findings are retained."
    return result


@mcp.tool()
@aws_operation('region')
def estimate_costs(region: str = "", time_period_days: int = 30, expected_account_id: str = "") -> dict:
    """Retrieve AWS spend data and detect cost anomalies via Cost Explorer."""
    months = max(1, time_period_days // 30)
    monthly_trend = get_monthly_trend(months=months)
    return {
        "current_month": get_current_month_cost(),
        "monthly_trend": monthly_trend,
        "by_service": get_cost_by_service(),
        "anomaly": detect_cost_anomaly(monthly_trend),
        "months_fetched": months,
    }


@mcp.tool()
@aws_operation('region')
async def get_cost_with_summary(
    region: str = "",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
    expected_account_id: str = "",
) -> dict:
    """Retrieve AWS cost data and generate an LLM cost-optimisation summary."""
    monthly_trend = get_monthly_trend(months=3)
    current_month = get_current_month_cost()
    by_service = get_cost_by_service()
    anomaly = detect_cost_anomaly(monthly_trend)

    cost_summary = {
        "current_month": current_month,
        "monthly_trend": monthly_trend,
        "by_service": by_service[:5],
        "anomaly": anomaly,
    }

    summary_prompt = (
        "You are an AWS cost-optimisation expert. Analyse this cost data and "
        "provide 3-4 actionable recommendations in plain English:\n\n"
        + json.dumps(cost_summary, indent=2, default=str)
    )
    llm_summary = await prompt_llm(summary_prompt, model, _resolve_key(model, api_key), model_name=ollama_model_name)

    return {
        "current_month": current_month,
        "monthly_trend": monthly_trend,
        "by_service": by_service,
        "anomaly": anomaly,
        "llm_summary": llm_summary,
    }


@mcp.tool()
@aws_operation('region')
async def generate_terraform_hcl(resource_type: str, config: dict, region: str = "", expected_account_id: str = "") -> dict:
    """Generate Terraform HCL from a resource type and config dict."""
    model = "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq"
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("GROQ_API_KEY", "")
    config_detail = (
        f" with the following configuration:\n{json.dumps(config, indent=2)}"
        if config
        else ""
    )
    request = f"Create a {resource_type}{config_detail}. Target AWS account {get_connection().account_id}, region {get_connection().region}."
    result = await generate_terraform(request, model, api_key)
    return {
        "hcl": result.get("hcl", ""),
        "explanation": result.get("description", ""),
        "valid": result.get("validation", {}).get("valid", False),
        "validation_message": result.get("validation", {}).get("message", ""),
        "resource_type": result.get("resource_type", "unknown"),
        "error": result.get("error"),
    }


@mcp.tool()
@aws_operation('aws_region')
async def generate_terraform_from_request(
    request: str,
    model: str = "groq",
    api_key: str = "",
    aws_region: str = "",
    expected_account_id: str = "",
) -> dict:
    """Generate Terraform HCL from a plain-English request string."""
    resolved_model = model or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq")
    resolved_key = _resolve_key(resolved_model, api_key)

    # Scan existing infra so the LLM knows what already exists
    existing_infra = ""
    try:
        existing_infra = await asyncio.to_thread(scan_existing_infra_for_context, get_connection().region)
    except Exception:
        existing_infra = ""
    existing_infra = f"TARGET AWS ACCOUNT: {get_connection().account_id}; REGION: {get_connection().region}. Use this region.\n" + existing_infra

    # Augment with knowledge base context if available
    if _RAG_AVAILABLE:
        try:
            rag_result = query_knowledge_base(request, n_results=3)
            if rag_result["chunks_used"] > 0:
                kb_chunks = "\n\n---\n\n".join(
                    f"[{c['metadata'].get('doc_id', 'unknown')}]\n{c['text']}"
                    for c in rag_result["raw_chunks"]
                )
                existing_infra = (
                    f"KNOWLEDGE BASE CONTEXT (follow these guidelines when generating HCL):\n\n"
                    f"{kb_chunks}\n\n"
                    + (existing_infra if existing_infra else "")
                ).strip()
        except Exception:
            pass

    result = await generate_terraform(request, resolved_model, resolved_key, existing_infra)

    naming_note = (
        "Resource names include a random suffix to prevent deployment conflicts."
        if result.get("hcl") and "random_id" in result.get("hcl", "")
        else None
    )

    return {
        "hcl": result.get("hcl", ""),
        "validation": result.get("validation", {"valid": False, "message": ""}),
        "resource_type": result.get("resource_type", "unknown"),
        "description": result.get("description", ""),
        "error": result.get("error"),
        "naming_note": naming_note,
    }


@mcp.tool()
def validate_terraform_plan(hcl: str) -> dict:
    """Validate Terraform HCL syntax without touching AWS."""
    result = validate_terraform_syntax(hcl)
    raw_message = result.get("message", "")
    errors, warnings = [], []
    if not result.get("valid", False):
        for line in raw_message.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "warning" in stripped.lower():
                warnings.append(stripped)
            else:
                errors.append(stripped)
    return {
        "valid": result.get("valid", False),
        "errors": errors,
        "warnings": warnings,
        "message": raw_message,
    }


@mcp.tool()
@aws_operation('aws_region')
async def run_terraform_plan_mcp(
    hcl_config: str, description: str = "", aws_region: str = "",
    expected_account_id: str = "", idempotency_key: str = "",
) -> dict:
    """Prepare an immutable saved plan for explicit digest-bound approval."""
    return await asyncio.to_thread(propose_terraform, hcl_config, description, idempotency_key)


@mcp.tool()
@aws_operation('aws_region', execution=True, mutating=True)
def run_terraform_apply_mcp(
    execution_id: str, approved: bool, aws_region: str = "", expected_account_id: str = "",
    approval_digest: str = "", idempotency_key: str = "",
) -> dict:
    """Claim and execute the reviewed action once, or reject it without AWS access."""
    result = decide(execution_id, approved, approval_digest, idempotency_key)
    result["key_files"] = [{"name": key["name"], "download_path": f"/terraform/keys/{execution_id}/{key['name']}"}
                           for key in result.get("key_files", [])]
    return result


@mcp.tool()
def get_execution_history_tool() -> dict:
    """Return all past Terraform plan/apply executions from the execution log."""
    from services.action_service import action_view
    store = get_execution_store()
    executions = []
    for entry in get_execution_history():
        view = action_view(entry)
        events = store.action_events(entry["execution_id"])
        if events:
            view["events"] = events
        executions.append(view)
    return {"executions": executions}


@mcp.tool()
def summarise_plan_for_human(
    plan_output: str,
    issue_being_fixed: str,
    risk_level: str = "medium",
) -> dict:
    """Parse raw terraform plan output and produce a plain-English approval summary."""
    return handle_summarise_plan(plan_output, issue_being_fixed, risk_level)


@mcp.tool()
@aws_operation('region')
async def aws_chat(
    message: str,
    model: str = "groq",
    api_key: str = "",
    history: list = None,
    region: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
    expected_account_id: str = "",
) -> dict:
    """Chat with an LLM about your live AWS infrastructure."""
    history = history or []
    resolved_key = _resolve_key(model, api_key)

    connection = get_connection()
    def build_context():
        return {**_collect_scan(connection.region),
                "cost_current_month": get_current_month_cost(), "cost_monthly_trend": get_monthly_trend(months=3),
                "cost_by_service": get_cost_by_service(), "sg_usage": scan_sg_usage(region=connection.region)}
    scan_data = cached_context(connection, build_context)

    augmented_message = message
    if _RAG_AVAILABLE:
        try:
            rag_result = query_knowledge_base(message, n_results=3)
            if rag_result["chunks_used"] > 0:
                context_parts = [
                    f"[{c['metadata'].get('doc_id', 'unknown')}]\n{c['text']}"
                    for c in rag_result["raw_chunks"]
                ]
                rag_prefix = (
                    "Relevant security knowledge base context:\n\n"
                    + "\n\n---\n\n".join(context_parts)
                    + "\n\n---\n\nUser question: "
                )
                augmented_message = rag_prefix + message
        except Exception:
            pass

    if model == "anthropic":
        key = resolved_key or os.getenv("ANTHROPIC_API_KEY")
        reply = await chat_with_anthropic(augmented_message, scan_data, history, key)
    elif model == "ollama":
        reply = await chat_with_ollama(augmented_message, scan_data, history, api_key=resolved_key, model_name=ollama_model_name)
    else:
        key = resolved_key or os.getenv("GROQ_API_KEY")
        reply = await chat_with_groq(augmented_message, scan_data, history, key)

    return {"reply": reply}


@mcp.tool()
@aws_operation('region')
async def agent_run(
    region: str = "",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
    expected_account_id: str = "",
) -> dict:
    """Autonomous security remediation agent — scan, pick top issue, generate fix, plan."""

    scan_data = _collect_scan(region)

    result = assessment(scan_data)
    if result["status"] != "complete":
        return {**result, "message": result["llm_summary"] + " Remediation was not started."}
    findings = result["findings"]
    if not findings:
        return {**result, "status": "no_issues", "message": result["llm_summary"]}

    issue = findings[0]

    fix_request = (
        f"Fix security issue: '{issue['title']}' on "
        f"{issue['resource_type']} {issue['resource_id']}. "
        f"{issue['recommendation']}"
    )
    resolved_model = (model or "").strip() or (
        "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq"
    )
    resolved_key = _resolve_key(resolved_model, api_key)

    # Security group open-port issues are fixed by revoking the 0.0.0.0/0 rule
    # directly via the AWS SDK. Terraform cannot manage rules it did not create,
    # so generating HCL for these always fails with duplicate-rule errors on retry.
    SG_REVOKE_RULES = ("SSH_PORT_OPEN", "RDP_PORT_OPEN", "UNRESTRICTED_ALL_TRAFFIC")
    if issue.get("rule") in SG_REVOKE_RULES:
        proposal = propose_ingress(issue["resource_id"], issue["metadata"].get("port", -1), scan_data["scan_id"])
        return {**proposal, "issue": issue, "summary": "Review the exact protocols, ranges and public sources below before approving."}

    # All other issue types use the Terraform generate → plan → apply flow.
    existing_infra = ""
    try:
        existing_infra = await asyncio.to_thread(scan_existing_infra_for_context, get_connection().region)
    except Exception:
        existing_infra = ""
    existing_infra = f"TARGET AWS ACCOUNT: {get_connection().account_id}; REGION: {get_connection().region}.\n" + existing_infra

    terraform_result = await generate_terraform(fix_request, resolved_model, resolved_key, existing_infra)
    if terraform_result.get("error"):
        return {"status": "error", "error": terraform_result["error"]}

    hcl = terraform_result.get("hcl", "")

    proposal = await asyncio.to_thread(propose_terraform, hcl, fix_request)
    return {**proposal, "issue": issue, "hcl": hcl, "summary": "Review the saved Terraform plan before approving."}


@mcp.tool()
@aws_operation('aws_region', execution=True, mutating=True)
def agent_approve(
    execution_id: str, approved: bool, aws_region: str = "", expected_account_id: str = "",
    approval_digest: str = "", idempotency_key: str = "",
) -> dict:
    """Approve/reject an agent proposal through the shared transactional executor."""
    return decide(execution_id, approved, approval_digest, idempotency_key)


@mcp.tool()
@aws_operation('region', scan=True)
def revoke_open_ingress_rule(
    sg_id: str, port: int, scan_id: str, region: str = "", expected_account_id: str = "", idempotency_key: str = "",
) -> dict:
    """Prepare exact public ingress removal for review. Does not mutate AWS."""
    return propose_ingress(sg_id, port, scan_id, idempotency_key)


@mcp.tool()
def mark_execution_resolved(execution_id: str) -> dict:
    """Mark a security finding execution as manually resolved in the execution log."""
    log_execution_update(execution_id, {
        "resolved":    True,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "resolved", "execution_id": execution_id}


@mcp.tool()
@aws_operation('aws_region', execution=True)
async def rollback_execution(
    execution_id: str, aws_region: str = "", expected_account_id: str = "", idempotency_key: str = "",
) -> dict:
    """Prepare a separate saved destroy plan. Approval is a distinct request."""
    return await asyncio.to_thread(propose_destroy, execution_id, idempotency_key)


try:
    from rag.knowledge_base import knowledge_base
    from rag.rag_service import query_knowledge_base
    _RAG_AVAILABLE = True
    # Auto-seed default AWS security docs the very first time the collection is
    # empty (e.g. fresh chroma_db/).  Skipped on every subsequent start once
    # any document exists, so user-added docs are never overwritten.
    if knowledge_base.get_document_count() == 0:
        from rag.seed_knowledge import seed_all
        seed_all()
except Exception:
    _RAG_AVAILABLE = False
    logging.getLogger(__name__).exception("Knowledge base initialization failed; /ready will report unavailable")


@mcp.tool()
async def rag_query_tool(
    question: str,
    n_results: int = 3,
    resource_type: str = "",
    model: str = "groq",
    api_key: str = "",
    groq_key: str = "",
) -> dict:
    """Search the security knowledge base and answer with LLM grounding."""
    if not _RAG_AVAILABLE:
        return {
            "answer": "RAG knowledge base is not available.",
            "sources": [],
            "chunks_used": 0,
            "raw_chunks": [],
        }

    result = query_knowledge_base(
        question,
        n_results=n_results,
        resource_filter=resource_type or None,
    )

    resolved_key = _resolve_key(model, api_key or groq_key)
    answer = await prompt_llm(result["augmented_prompt"], model=model, api_key=resolved_key)

    return {
        "answer": answer,
        "sources": result["sources"],
        "chunks_used": result["chunks_used"],
        "raw_chunks": result["raw_chunks"],
    }


@mcp.tool()
def rag_list_documents() -> dict:
    """List all documents currently stored in the ChromaDB knowledge base."""
    if not _RAG_AVAILABLE:
        return {"documents": []}

    try:
        collection = knowledge_base.collection
        result = collection.get(include=["metadatas"])
        docs: dict = {}
        for meta in result.get("metadatas") or []:
            doc_id = meta.get("doc_id", "unknown")
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "resource_type": meta.get("resource_type", "general"),
                    "chunk_count": 0,
                }
            docs[doc_id]["chunk_count"] += 1
        return {"documents": list(docs.values())}
    except Exception as e:
        return {"documents": [], "error": str(e)}


@mcp.tool()
def rag_add_text_document(
    doc_id: str,
    text: str,
    resource_type: str = "general",
) -> dict:
    """Add a text document to the ChromaDB knowledge base."""
    if not _RAG_AVAILABLE:
        return {"chunks_added": 0, "doc_id": doc_id, "error": "RAG not available"}

    result = knowledge_base.add_document(
        doc_id=doc_id,
        text=text,
        metadata={"resource_type": resource_type},
    )
    return {"chunks_added": result.get("chunks_added", 0), "doc_id": doc_id}


@mcp.tool()
def rag_delete_document(doc_id: str) -> dict:
    """Delete a document and all its chunks from the knowledge base."""
    if not _RAG_AVAILABLE:
        return {"deleted": False, "message": "RAG not available"}

    try:
        collection = knowledge_base.collection
        results = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
        ids = results.get("ids", [])
        if not ids:
            return {"deleted": False, "message": f"No chunks found for '{doc_id}'"}
        collection.delete(ids=ids)
        return {
            "deleted": True,
            "message": f"Deleted {len(ids)} chunks for '{doc_id}'",
        }
    except Exception as e:
        return {"deleted": False, "message": str(e)}


@mcp.tool()
def rag_upload_file(
    doc_id: str,
    file_content_base64: str,
    filename: str,
    resource_type: str = "general",
) -> dict:
    """Add a file (PDF or plain text) to the knowledge base from base64-encoded content."""
    import base64
    import io

    if not _RAG_AVAILABLE:
        return {"chunks_added": 0, "doc_id": doc_id, "error": "RAG not available"}

    try:
        file_bytes = base64.b64decode(file_content_base64)
    except Exception as e:
        return {"chunks_added": 0, "doc_id": doc_id, "error": f"Base64 decode error: {e}"}

    if filename.lower().endswith(".pdf"):
        try:
            import PyPDF2
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            pages = [page.extract_text() or "" for page in pdf_reader.pages]
            text = "\n\n".join(pages).strip()
        except Exception as e:
            return {"chunks_added": 0, "doc_id": doc_id, "error": f"PDF parse error: {e}"}
    else:
        text = file_bytes.decode("utf-8", errors="replace").strip()

    if not text:
        return {
            "chunks_added": 0,
            "doc_id": doc_id,
            "error": "No text could be extracted from the file.",
        }

    result = knowledge_base.add_document(
        doc_id=doc_id,
        text=text,
        metadata={"resource_type": resource_type, "filename": filename},
    )
    return {"chunks_added": result.get("chunks_added", 0), "doc_id": doc_id, "error": None}


@mcp.tool()
async def ollama_status(base_url: str = "http://localhost:11434") -> dict:
    """Probe a local Ollama instance and list installed models."""
    return await probe_ollama(base_url)


@mcp.resource("aws://findings/{region}")
@aws_operation('region')
def aws_findings_resource(region: str) -> dict:
    """Latest security findings for an AWS region, pulled on demand.

    URI pattern: aws://findings/{region}
    Example:     aws://findings/us-east-1
    """
    scan_data = _collect_scan(region)
    return {**assessment(scan_data), "region": region}


@mcp.resource("aws://cost-summary/{region}")
@aws_operation('region')
def aws_cost_summary_resource(region: str) -> dict:
    """Latest AWS cost summary for the account, pulled on demand.

    URI pattern: aws://cost-summary/{region}
    Note: Cost Explorer is global — region is informational only.
    """
    monthly_trend = get_monthly_trend(months=3)
    return {
        "region": region,
        "current_month": get_current_month_cost(),
        "monthly_trend": monthly_trend,
        "by_service": get_cost_by_service(),
        "anomaly": detect_cost_anomaly(monthly_trend),
    }


async def _ollama_status_endpoint(request: Request) -> JSONResponse:
    """GET /api/ollama/status?base_url=..."""
    base_url = request.query_params.get("base_url", "http://localhost:11434")
    return JSONResponse(await probe_ollama(base_url))


_PULL_CATALOG_IDS = {m["id"] for m in MODELS}


async def _ollama_pull_endpoint(request: Request):
    """POST /api/ollama/pull — streams JSONL pull progress from Ollama. Returns 400 if the model is not in the catalog."""
    body = await request.json()
    model = body.get("model", "")
    base_url = body.get("base_url", "http://localhost:11434")

    if model not in _PULL_CATALOG_IDS:
        return JSONResponse({"error": "model_not_in_catalog"}, status_code=400)

    return StreamingResponse(
        stream_ollama_pull(base_url, model),
        media_type="application/x-ndjson",
    )


def create_app():
    """Compatibility entrypoint delegating to the shared HTTP application."""
    from http_app import create_app as build_http_app

    return build_http_app()


if __name__ == "__main__":
    import sys

    # Reuse this module when the shared factory imports mcp_server. Otherwise
    # `python mcp_server.py` would register and seed the tools a second time.
    sys.modules["mcp_server"] = sys.modules[__name__]
    from services.storage import get_execution_store
    get_execution_store().initialize()

    # stdio mode: launched by Claude Desktop as a subprocess.
    # Pass --stdio (or set MCP_TRANSPORT=stdio) to enable.
    if "--stdio" in sys.argv or os.getenv("MCP_TRANSPORT") == "stdio":
        mcp.run()  # FastMCP stdio transport — Claude Desktop reads/writes stdin/stdout
    else:
        # HTTP mode: React frontend connects via JSON-RPC at POST /mcp.
        app = create_app()
        print("MCP server starting on http://localhost:8000")
        print("  MCP endpoint : POST http://localhost:8000/mcp")
        print("  File upload  : POST http://localhost:8000/rag/documents/upload")
        print("  Claude Code  : add url http://localhost:8000/mcp to MCP config")
        print("  Claude Desktop: run with --stdio flag instead")
        uvicorn.run(app, host="0.0.0.0", port=8000)
