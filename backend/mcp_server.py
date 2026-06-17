# MCP server for the Agentic Cloud Assistant — exposes all capabilities as tools over POST /mcp.

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from cachetools import TTLCache
from fastmcp import FastMCP
from fastmcp.server.http import create_streamable_http_app
from starlette.middleware.cors import CORSMiddleware
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
    revoke_sg_ingress_rule,
    scan_sg_usage,
)
from services.cost_analyzer import (
    detect_cost_anomaly,
    get_cost_by_service,
    get_current_month_cost,
    get_monthly_trend,
)
from services.execution_service import (
    cleanup_workdir_plugins,
    create_execution_id,
    get_execution_history,
    log_execution,
    log_execution_update,
    purge_old_workdirs,
    run_terraform_apply,
    run_terraform_destroy,
    run_terraform_plan,
)
from services.llm_service import (
    chat_with_anthropic,
    chat_with_groq,
    chat_with_ollama,
    prompt_llm,
)
from ollama_catalog import MODELS, DEFAULT_MODEL
from services.ollama_service import probe_ollama, stream_ollama_pull
from services.security_analyzer import run_security_analysis
from services.terraform_service import (
    generate_terraform,
    handle_summarise_plan,
    validate_terraform_syntax,
)

mcp = FastMCP("agentic-cloud-assistant")

# Keyed by region, refreshed every 5 minutes. Avoids re-scanning on every chat message.
_SCAN_CACHE: TTLCache = TTLCache(maxsize=10, ttl=300)


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
def full_aws_scan(region: str = "us-east-1") -> dict:
    """Run all five AWS service scans and return combined infrastructure data."""
    return {
        "ec2": scan_ec2(region=region),
        "s3": scan_s3(),
        "iam": scan_iam(),
        "security_groups": scan_security_groups(region=region),
        "vpc": scan_vpc(region=region),
    }


@mcp.tool()
def scan_ec2_instances(region: str = "us-east-1") -> dict:
    """Scan all EC2 instances in the specified AWS region."""
    return scan_ec2(region=region)


@mcp.tool()
def scan_s3_buckets() -> dict:
    """Scan all S3 buckets and check public-access status."""
    return scan_s3()


@mcp.tool()
def scan_iam_users() -> dict:
    """Scan all IAM users and check MFA status and last-login date."""
    return scan_iam()


@mcp.tool()
def scan_security_groups_detail(region: str = "us-east-1") -> dict:
    """Scan all EC2 security groups and flag dangerous internet-facing rules."""
    return scan_security_groups(region=region)


@mcp.tool()
def scan_vpc_detail(region: str = "us-east-1") -> dict:
    """Scan all VPCs including subnet counts."""
    return scan_vpc(region=region)


@mcp.tool()
def scan_sg_usage_tool(
    region: str = "us-east-1",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
) -> dict:
    """Map every security group to the resources currently attached to it."""
    credentials = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            region,
    } if aws_access_key_id else None

    return scan_sg_usage(region=region, credentials=credentials)


@mcp.tool()
def analyse_security_findings(scan_data: dict) -> dict:
    """Run 7 built-in security rules against raw AWS scan data."""
    findings = run_security_analysis(scan_data)
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        sev = f.get("severity", "LOW")
        counts[sev] = counts.get(sev, 0) + 1
    return {"total_findings": len(findings), "severity_counts": counts, "findings": findings}


@mcp.tool()
async def run_security_analysis_with_summary(
    region: str = "us-east-1",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
) -> dict:
    """Scan AWS, run security rules, and generate an LLM plain-English summary."""
    scan_data = {
        "ec2": scan_ec2(region=region),
        "s3": scan_s3(),
        "iam": scan_iam(),
        "security_groups": scan_security_groups(region=region),
        "vpc": scan_vpc(region=region),
    }

    findings = run_security_analysis(scan_data)
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        sev = f.get("severity", "LOW")
        counts[sev] = counts.get(sev, 0) + 1

    if findings:
        summary_prompt = (
            f"You are an AWS security expert. Summarise these {len(findings)} "
            f"security findings in 2-3 concise paragraphs for a cloud engineer:\n\n"
            + json.dumps(
                [
                    {
                        "severity": f.get("severity"),
                        "title": f.get("title"),
                        "resource_id": f.get("resource_id"),
                        "recommendation": f.get("recommendation"),
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
        llm_summary = await prompt_llm(summary_prompt, model, _resolve_key(model, api_key), model_name=ollama_model_name)
    else:
        llm_summary = "No security issues found. Your AWS infrastructure looks clean."

    return {
        "findings": findings,
        "severity_counts": counts,
        "total_findings": len(findings),
        "llm_summary": llm_summary,
    }



@mcp.tool()
async def run_security_analysis_with_summary1(
    region: str = "us-east-1",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
) -> dict:
    """Scan AWS, run security rules, and generate an LLM plain-English summary."""
    scan_data = {
        "ec2": scan_ec2(region=region),
        "s3": scan_s3(),
        "iam": scan_iam(),
        "security_groups": scan_security_groups(region=region),
        "vpc": scan_vpc(region=region),
    }

    findings = run_security_analysis(scan_data)
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        sev = f.get("severity", "LOW")
        counts[sev] = counts.get(sev, 0) + 1

    if findings:
        summary_prompt = (
            f"You are an AWS security expert. Summarise these {len(findings)} "
            f"security findings in 2-3 concise paragraphs for a cloud engineer:\n\n"
            + json.dumps(
                [
                    {
                        "severity": f.get("severity"),
                        "title": f.get("title"),
                        "resource_id": f.get("resource_id"),
                        "recommendation": f.get("recommendation"),
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
        llm_summary = await prompt_llm(summary_prompt, model, _resolve_key(model, api_key), model_name=ollama_model_name)
    else:
        llm_summary = "No security issues found. Your AWS infrastructure looks clean."

    return {
        "findings": findings,
        "severity_counts": counts,
        "total_findings": len(findings),
        "llm_summary": llm_summary,
    }





@mcp.tool()
def estimate_costs(region: str = "us-east-1", time_period_days: int = 30) -> dict:
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
async def get_cost_with_summary(
    region: str = "us-east-1",
    model: str = "groq",
    api_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
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
async def generate_terraform_hcl(resource_type: str, config: dict) -> dict:
    """Generate Terraform HCL from a resource type and config dict."""
    model = "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq"
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("GROQ_API_KEY", "")
    config_detail = (
        f" with the following configuration:\n{json.dumps(config, indent=2)}"
        if config
        else ""
    )
    request = f"Create a {resource_type}{config_detail}"
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
async def generate_terraform_from_request(
    request: str,
    model: str = "groq",
    api_key: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """Generate Terraform HCL from a plain-English request string."""
    resolved_model = model or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq")
    resolved_key = _resolve_key(resolved_model, api_key)

    # Scan existing infra so the LLM knows what already exists
    existing_infra = ""
    if aws_access_key_id:
        creds = {
            "aws_access_key_id":     aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
            "aws_region":            aws_region,
        }
        try:
            existing_infra = await asyncio.to_thread(
                scan_existing_infra_for_context, aws_region, creds
            )
        except Exception:
            existing_infra = ""

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
async def run_terraform_plan_mcp(
    hcl_config: str,
    description: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """Write HCL to a persistent working directory, run terraform init + plan."""
    execution_id = create_execution_id()
    aws_creds = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            aws_region,
    } if aws_access_key_id else None
    plan_result = await asyncio.to_thread(run_terraform_plan, hcl_config, execution_id, aws_creds)

    status = "awaiting_approval" if plan_result["success"] else "plan_failed"
    log_execution(
        {
            "execution_id": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "description": description or "Terraform plan",
            "plan_output": plan_result.get("plan_output", ""),
            "resources_to_add": plan_result.get("resources_to_add", 0),
            "resources_to_change": plan_result.get("resources_to_change", 0),
            "resources_to_destroy": plan_result.get("resources_to_destroy", 0),
        }
    )

    return {
        "execution_id": execution_id,
        "status": status,
        "plan_output": plan_result.get("plan_output", ""),
        "resources_to_add": plan_result.get("resources_to_add", 0),
        "resources_to_change": plan_result.get("resources_to_change", 0),
        "resources_to_destroy": plan_result.get("resources_to_destroy", 0),
    }


@mcp.tool()
def run_terraform_apply_mcp(
    execution_id: str,
    approved: bool,
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """Apply or reject a previously planned Terraform execution."""
    if not approved:
        log_execution_update(execution_id, {"status": "rejected"})
        cleanup_workdir_plugins(execution_id)
        return {"status": "rejected", "apply_output": "", "resources_applied": []}

    aws_creds = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            aws_region,
    } if aws_access_key_id else None
    apply_result = run_terraform_apply(execution_id, aws_creds)
    status = "complete" if apply_result.get("success") else "failed"
    log_execution_update(
        execution_id,
        {
            "status": status,
            "apply_output": apply_result.get("apply_output", ""),
            "resources_applied": apply_result.get("resources_applied", []),
        },
    )
    return {
        "status": status,
        "apply_output": apply_result.get("apply_output", ""),
        "resources_applied": apply_result.get("resources_applied", []),
        "key_files": [
            {"name": kf["name"], "download_path": f"/terraform/keys/{execution_id}/{kf['name']}"}
            for kf in apply_result.get("key_files", [])
        ],
    }


@mcp.tool()
def get_execution_history_tool() -> dict:
    """Return all past Terraform plan/apply executions from the execution log."""
    return {"executions": get_execution_history()}


@mcp.tool()
def summarise_plan_for_human(
    plan_output: str,
    issue_being_fixed: str,
    risk_level: str = "medium",
) -> dict:
    """Parse raw terraform plan output and produce a plain-English approval summary."""
    return handle_summarise_plan(plan_output, issue_being_fixed, risk_level)


@mcp.tool()
async def aws_chat(
    message: str,
    model: str = "groq",
    api_key: str = "",
    history: list = None,
    region: str = "us-east-1",
    ollama_model_name: str = DEFAULT_MODEL,
) -> dict:
    """Chat with an LLM about your live AWS infrastructure."""
    history = history or []
    resolved_key = _resolve_key(model, api_key)

    # Include the first 12 chars of the access key in the cache key so two users
    # with different AWS accounts in the same region get independent cache entries.
    _env_key_id = os.getenv("AWS_ACCESS_KEY_ID", "env-default")
    _cache_key  = (region, _env_key_id[:12])
    if _cache_key not in _SCAN_CACHE:
        _SCAN_CACHE[_cache_key] = {
            "ec2": scan_ec2(region=region),
            "s3": scan_s3(),
            "iam": scan_iam(),
            "security_groups": scan_security_groups(region=region),
            "vpc": scan_vpc(region=region),
            "cost_current_month": get_current_month_cost(),
            "cost_monthly_trend": get_monthly_trend(months=3),
            "cost_by_service": get_cost_by_service(),
            "sg_usage": scan_sg_usage(region=region),
        }
    scan_data = _SCAN_CACHE[_cache_key]

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
async def agent_run(
    region: str = "us-east-1",
    model: str = "groq",
    api_key: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    ollama_model_name: str = DEFAULT_MODEL,
) -> dict:
    """Autonomous security remediation agent — scan, pick top issue, generate fix, plan."""
    aws_creds = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            region,
    } if aws_access_key_id else None

    scan_data = {
        "ec2": scan_ec2(region=region, credentials=aws_creds),
        "s3": scan_s3(credentials=aws_creds),
        "iam": scan_iam(credentials=aws_creds),
        "security_groups": scan_security_groups(region=region, credentials=aws_creds),
        "vpc": scan_vpc(region=region, credentials=aws_creds),
    }

    findings = run_security_analysis(scan_data)
    if not findings:
        return {"status": "no_issues", "message": "No security issues found."}

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

    execution_id = create_execution_id()

    # Security group open-port issues are fixed by revoking the 0.0.0.0/0 rule
    # directly via the AWS SDK. Terraform cannot manage rules it did not create,
    # so generating HCL for these always fails with duplicate-rule errors on retry.
    SG_REVOKE_RULES = ("SSH_PORT_OPEN", "RDP_PORT_OPEN", "UNRESTRICTED_ALL_TRAFFIC")
    if issue.get("rule") in SG_REVOKE_RULES:
        sg_id = issue["resource_id"]
        port  = issue["metadata"].get("port", -1)  # -1 matches all-traffic (protocol=-1) rules

        plan_output = (
            f"DIRECT AWS API ACTION — no Terraform state affected.\n\n"
            f"Target:  {sg_id}\n"
            f"Action:  Revoke all inbound rules open to 0.0.0.0/0 or ::/0 on port {port}\n"
            f"Method:  ec2:RevokeSecurityGroupIngress\n\n"
            f"This removes the dangerous open rule directly. "
            f"If the rule is already gone this is a safe no-op."
        )
        hcl = (
            f"# Direct AWS SDK action — no Terraform HCL required.\n"
            f"# Will revoke 0.0.0.0/0 and ::/0 inbound rules on port {port}\n"
            f"# for security group {sg_id} via ec2:RevokeSecurityGroupIngress."
        )
        summary_prompt = (
            f"In 2-3 sentences for a non-expert, explain what this security fix does:\n\n"
            f"Finding: {issue['title']}\n"
            f"Fix: Remove the inbound rule that allows port {port} access from any IP "
            f"address (0.0.0.0/0) on security group {sg_id}."
        )
        summary = await prompt_llm(summary_prompt, resolved_model, resolved_key, model_name=ollama_model_name)

        log_execution({
            "execution_id": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "awaiting_approval",
            "description": fix_request,
            "issue": issue,
            "hcl": hcl,
            "action_type": "sg_revoke",
            "sg_id": sg_id,
            "port": port,
            "plan_output": plan_output,
            "resources_to_add": 0,
            "resources_to_change": 1,
            "resources_to_destroy": 0,
        })

        return {
            "status": "awaiting_approval",
            "execution_id": execution_id,
            "issue": issue,
            "hcl": hcl,
            "plan_output": plan_output,
            "resources_to_add": 0,
            "resources_to_change": 1,
            "resources_to_destroy": 0,
            "summary": summary,
        }

    # All other issue types use the Terraform generate → plan → apply flow.
    existing_infra = ""
    if aws_creds:
        try:
            existing_infra = await asyncio.to_thread(
                scan_existing_infra_for_context, region, aws_creds
            )
        except Exception:
            existing_infra = ""

    terraform_result = await generate_terraform(fix_request, resolved_model, resolved_key, existing_infra)
    if terraform_result.get("error"):
        return {"status": "error", "error": terraform_result["error"]}

    hcl = terraform_result.get("hcl", "")

    plan_result = await asyncio.to_thread(run_terraform_plan, hcl, execution_id, aws_creds)

    plan_snippet = plan_result.get("plan_output", "")[:2000]
    summary_prompt = (
        f"Summarise this planned AWS infrastructure change in 2-3 sentences "
        f"for a non-expert to review before approving:\n\n"
        f"Issue: {issue['title']}\n"
        f"Resource: {issue['resource_type']} {issue['resource_id']}\n"
        f"Changes: +{plan_result.get('resources_to_add',0)} "
        f"~{plan_result.get('resources_to_change',0)} "
        f"-{plan_result.get('resources_to_destroy',0)}\n\n"
        f"Plan:\n{plan_snippet}"
    )
    summary = await prompt_llm(summary_prompt, resolved_model, resolved_key, model_name=ollama_model_name)

    log_execution(
        {
            "execution_id": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "awaiting_approval",
            "description": fix_request,
            "issue": issue,
            "hcl": hcl,
            "plan_output": plan_result.get("plan_output", ""),
            "resources_to_add": plan_result.get("resources_to_add", 0),
            "resources_to_change": plan_result.get("resources_to_change", 0),
            "resources_to_destroy": plan_result.get("resources_to_destroy", 0),
        }
    )

    return {
        "status": "awaiting_approval",
        "execution_id": execution_id,
        "issue": issue,
        "hcl": hcl,
        "plan_output": plan_result.get("plan_output", ""),
        "resources_to_add": plan_result.get("resources_to_add", 0),
        "resources_to_change": plan_result.get("resources_to_change", 0),
        "resources_to_destroy": plan_result.get("resources_to_destroy", 0),
        "summary": summary,
    }


@mcp.tool()
def agent_approve(
    execution_id: str,
    approved: bool,
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """Approve or reject an agent-generated fix plan."""
    if not approved:
        log_execution_update(
            execution_id,
            {
                "status": "rejected",
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "rejected", "apply_output": "", "resources_applied": []}

    history = get_execution_history()
    entry = next((e for e in history if e.get("execution_id") == execution_id), {})

    if entry.get("action_type") == "sg_revoke":
        sg_id = entry.get("sg_id", "")
        port  = int(entry.get("port", 22))
        creds = {
            "aws_access_key_id":     aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
            "aws_region":            aws_region,
        } if aws_access_key_id else None
        result  = revoke_sg_ingress_rule(sg_id, port, aws_region, creds)
        success = result.get("success", False)
        status  = "complete" if success else "failed"
        msg     = result.get("message", "")
        log_execution_update(
            execution_id,
            {
                "status": status,
                "apply_output": msg,
                "resources_applied": [sg_id] if success else [],
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {
            "status": status,
            "apply_output": msg,
            "resources_applied": [sg_id] if success else [],
            "error": msg if not success else None,
        }

    aws_creds = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            aws_region,
    } if aws_access_key_id else None
    apply_result = run_terraform_apply(execution_id, aws_creds)
    status = "complete" if apply_result.get("success") else "failed"
    log_execution_update(
        execution_id,
        {
            "status": status,
            "apply_output": apply_result.get("apply_output", ""),
            "resources_applied": apply_result.get("resources_applied", []),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "status": status,
        "apply_output": apply_result.get("apply_output", ""),
        "resources_applied": apply_result.get("resources_applied", []),
        "error": apply_result.get("apply_output", "") if status == "failed" else None,
    }


@mcp.tool()
def revoke_open_ingress_rule(
    sg_id: str,
    port: int,
    region: str = "us-east-1",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
) -> dict:
    """Revoke all 0.0.0.0/0 and ::/0 ingress rules for a specific port on a security group."""
    credentials = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            region,
    } if aws_access_key_id else None

    return revoke_sg_ingress_rule(sg_id, port, region, credentials)


@mcp.tool()
def mark_execution_resolved(execution_id: str) -> dict:
    """Mark a security finding execution as manually resolved in the execution log."""
    log_execution_update(execution_id, {
        "resolved":    True,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "resolved", "execution_id": execution_id}


@mcp.tool()
async def rollback_execution(
    execution_id: str,
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """Destroy resources created by a previous terraform apply (rollback)."""
    aws_creds = {
        "aws_access_key_id":     aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "aws_region":            aws_region,
    } if aws_access_key_id else None

    result = await asyncio.to_thread(run_terraform_destroy, execution_id, aws_creds)
    status = "rolled_back" if result["success"] else "rollback_failed"
    log_execution_update(execution_id, {
        "status":         status,
        "destroy_output": result.get("destroy_output", ""),
    })
    return {"status": status, "destroy_output": result.get("destroy_output", "")}


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
def aws_findings_resource(region: str) -> dict:
    """Latest security findings for an AWS region, pulled on demand.

    URI pattern: aws://findings/{region}
    Example:     aws://findings/us-east-1
    """
    scan_data = {
        "ec2": scan_ec2(region=region),
        "s3": scan_s3(),
        "iam": scan_iam(),
        "security_groups": scan_security_groups(region=region),
        "vpc": scan_vpc(region=region),
    }
    findings = run_security_analysis(scan_data)
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f.get("severity", "LOW")] = counts.get(f.get("severity", "LOW"), 0) + 1
    return {
        "region": region,
        "total_findings": len(findings),
        "severity_counts": counts,
        "findings": findings,
    }


@mcp.resource("aws://cost-summary/{region}")
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


async def _upload_document_endpoint(request: Request) -> JSONResponse:
    """POST /rag/documents/upload — multipart file upload to ChromaDB."""
    import io
    try:
        form = await request.form()
        file = form.get("file")
        doc_id = str(form.get("doc_id", ""))
        resource_type = str(form.get("resource_type", "general"))

        if not file or not doc_id.strip():
            return JSONResponse(
                {"message": "file and doc_id are required"}, status_code=422
            )

        if not _RAG_AVAILABLE:
            return JSONResponse({"message": "RAG not available"}, status_code=500)

        content = await file.read()
        filename = getattr(file, "filename", "") or ""

        if filename.lower().endswith(".pdf"):
            try:
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
                pages = [page.extract_text() or "" for page in pdf_reader.pages]
                text = "\n\n".join(pages).strip()
            except Exception as e:
                return JSONResponse({"message": f"PDF parse error: {e}"}, status_code=422)
        else:
            text = content.decode("utf-8", errors="replace").strip()

        if not text:
            return JSONResponse(
                {"message": "No text could be extracted from the file."}, status_code=422
            )

        result = knowledge_base.add_document(
            doc_id=doc_id,
            text=text,
            metadata={"resource_type": resource_type, "filename": filename},
        )
        return JSONResponse(
            {"chunks_added": result.get("chunks_added", 0), "doc_id": doc_id}
        )
    except Exception as e:
        return JSONResponse({"message": str(e)}, status_code=500)


def create_app():
    from starlette.routing import Route

    base_app = create_streamable_http_app(mcp, streamable_http_path="/mcp")

    base_app.router.routes.append(
        Route("/rag/documents/upload", _upload_document_endpoint, methods=["POST"])
    )
    base_app.router.routes.append(
        Route("/api/ollama/status", _ollama_status_endpoint, methods=["GET"])
    )
    base_app.router.routes.append(
        Route("/api/ollama/pull", _ollama_pull_endpoint, methods=["POST"])
    )

    base_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )

    return base_app


if __name__ == "__main__":
    import sys

    # Recover disk space from stranded workdirs left by previous sessions.
    # Removes plan-only dirs (no tfstate) older than 7 days; keeps applied dirs for rollback.
    _purge = purge_old_workdirs(older_than_days=7)
    if _purge["removed"]:
        print(f"Startup cleanup: removed {_purge['removed']} old workdirs "
              f"({_purge['freed_bytes'] / 1_073_741_824:.1f} GB freed)")

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
