"""
docs_generator.py

Reads every tool registered on a FastMCP instance and auto-creates:
  - A Pydantic input model per tool  → Swagger shows correct schema + validation
  - A FastAPI POST route per tool    → "Try it out" fires a real live request
  - A handler per route              → calls the tool via FastMCP in-process Client

Usage (called once in main.py at startup, before uvicorn starts):
    from docs_generator import register_mcp_docs_routes
    register_mcp_docs_routes(app, mcp)

How it stays in sync automatically:
    Every @mcp.tool() in mcp_server.py is registered into FastMCP's internal
    component registry at import time. This function reads that registry
    synchronously — no async needed. The next server restart picks up any new
    or removed tools without any manual documentation updates.
"""

from typing import Any, Optional

from fastapi import FastAPI
from fastmcp import Client
from pydantic import Field, create_model

_TOOL_GROUPS: dict[str, str] = {
    "health_check":                       "Health",
    "full_aws_scan":                      "AWS Scanning",
    "scan_ec2_instances":                 "AWS Scanning",
    "scan_s3_buckets":                    "AWS Scanning",
    "scan_iam_users":                     "AWS Scanning",
    "scan_security_groups_detail":        "AWS Scanning",
    "scan_vpc_detail":                    "AWS Scanning",
    "analyse_security_findings":          "Security",
    "run_security_analysis_with_summary": "Security",
    "estimate_costs":                     "Cost",
    "get_cost_with_summary":              "Cost",
    "generate_terraform_hcl":             "Terraform",
    "generate_terraform_from_request":    "Terraform",
    "validate_terraform_plan":            "Terraform",
    "run_terraform_plan_mcp":             "Terraform",
    "run_terraform_apply_mcp":            "Terraform",
    "summarise_plan_for_human":           "Terraform",
    "get_execution_history_tool":         "Terraform",
    "aws_chat":                           "Chat",
    "agent_run":                          "Agent",
    "agent_approve":                      "Agent",
    "rag_query_tool":                     "Knowledge Base",
    "rag_list_documents":                 "Knowledge Base",
    "rag_add_text_document":              "Knowledge Base",
    "rag_delete_document":                "Knowledge Base",
    "rag_upload_file":                    "Knowledge Base",
}

# Maps JSON Schema primitive types → (Python type, safe default value)
_JSON_TYPE_MAP: dict[str, tuple] = {
    "string":  (str,   ""),
    "integer": (int,   0),
    "number":  (float, 0.0),
    "boolean": (bool,  False),
    "array":   (list,  []),
    "object":  (dict,  {}),
}


def _build_input_model(tool_name: str, schema: dict):
    """
    Convert a JSON Schema dict (from tool.parameters) into a Pydantic BaseModel.

    Returns None for tools that take no arguments — those get a no-body handler.

    Example schema input:
        {
          "properties": {
            "region":  {"type": "string",  "default": "us-east-1"},
            "model":   {"type": "string",  "default": "groq"},
            "api_key": {"type": "string",  "default": ""}
          }
        }

    Produces the equivalent of:
        class run_security_analysis_with_summaryInput(BaseModel):
            region:  Optional[str] = "us-east-1"
            model:   Optional[str] = "groq"
            api_key: Optional[str] = ""
    """
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    if not properties:
        return None  # no-arg tool — no request body needed

    fields: dict = {}
    for field_name, field_info in properties.items():
        json_type = field_info.get("type", "string")
        py_type, type_default = _JSON_TYPE_MAP.get(json_type, (Any, None))

        is_required = field_name in required_fields
        description = field_info.get("description", "")
        default = field_info.get("default", ... if is_required else type_default)

        # Required fields use the bare type; optional fields use Optional[type]
        annotation = py_type if is_required else Optional[py_type]
        fields[field_name] = (annotation, Field(default=default, description=description))

    return create_model(f"{tool_name}Input", **fields)


def _make_handler(tool_name: str, mcp_instance, input_model):
    """
    Build a FastAPI async handler for a single MCP tool.

    Two variants:
      - With input model:    handler(body: InputModel) — Swagger shows the schema
      - Without input model: handler()                 — tool takes no arguments

    The handler calls the tool via FastMCP's in-process Client.
    This goes through no HTTP — it is a direct in-memory function call,
    so there is zero network overhead compared to calling the tool directly.

    result.data is FastMCP 3.x's already-parsed Python object — no JSON
    parsing needed. Falls back to result.content[0].text if data is None.
    """
    if input_model is not None:
        async def handler(body: input_model):  # type: ignore[valid-type]
            args = body.model_dump(exclude_none=True)
            async with Client(mcp_instance) as client:
                result = await client.call_tool(tool_name, args)
            if result.is_error:
                return {"error": str(result.data)}
            if isinstance(result.data, (dict, list)):
                return result.data
            if result.data is not None:
                return {"result": result.data}
            # Fallback: parse content text
            if result.content:
                import json as _json
                try:
                    return _json.loads(result.content[0].text)
                except Exception:
                    return {"result": result.content[0].text}
            return {}
    else:
        async def handler():  # type: ignore[misc]
            async with Client(mcp_instance) as client:
                result = await client.call_tool(tool_name, {})
            if result.is_error:
                return {"error": str(result.data)}
            if isinstance(result.data, (dict, list)):
                return result.data
            if result.data is not None:
                return {"result": result.data}
            if result.content:
                import json as _json
                try:
                    return _json.loads(result.content[0].text)
                except Exception:
                    return {"result": result.content[0].text}
            return {}

    # FastAPI uses __name__ as the operationId in the OpenAPI spec
    handler.__name__ = tool_name
    return handler


def register_mcp_docs_routes(app: FastAPI, mcp_instance) -> None:
    """
    Read all tools from the FastMCP instance and register a documented
    FastAPI POST route for each one. Call once at startup in main.py.

    Accessing _local_provider._components is synchronous — no event loop
    required. The component dict is populated at import time when each
    @mcp.tool() decorator runs, so all tools are available immediately.

    After this call, Swagger at GET /docs shows every MCP tool as a
    documented, testable POST /tools/{tool_name} endpoint.
    """
    components = mcp_instance._local_provider._components

    tool_entries = {k: v for k, v in components.items() if k.startswith("tool:")}

    for _key, tool in tool_entries.items():
        name        = tool.name
        description = tool.description or name
        schema      = tool.parameters or {}
        tag         = _TOOL_GROUPS.get(name, "Tools")

        input_model = _build_input_model(name, schema)
        handler     = _make_handler(name, mcp_instance, input_model)

        app.add_api_route(
            path          = f"/tools/{name}",
            endpoint      = handler,
            methods       = ["POST"],
            summary       = description,
            tags          = [tag],
            response_model= None,  # dict/list return — FastAPI infers from response
        )
