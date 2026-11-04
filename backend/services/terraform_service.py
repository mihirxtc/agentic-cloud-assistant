import json
import os
import re

import anthropic
import httpx
from groq import Groq


# TERRAFORM_TOOL is still used by generate_terraform_with_anthropic to force
# structured output (tool_choice) from the Anthropic API. It is NOT the old
# Tool Use API agentic loop — that pattern has been removed.
TERRAFORM_TOOL = {
    "name": "generate_terraform",
    "description": "Generate complete, valid Terraform HCL for an AWS resource.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hcl": {
                "type": "string",
                "description": "Complete Terraform HCL including terraform{} with server-pinned aws=5.100.0 and random=3.7.2 providers, a random_id suffix resource, provider{}, variable{}, and all resource blocks. Every AWS resource name MUST include ${random_id.suffix.hex} to guarantee uniqueness.",
            },
            "resource_type": {
                "type": "string",
                "description": "Primary AWS resource type created (e.g. aws_instance, aws_s3_bucket).",
            },
            "description": {
                "type": "string",
                "description": "One-sentence plain-English description of what the config creates.",
            },
        },
        "required": ["hcl", "resource_type", "description"],
    },
}


SYSTEM_PROMPT = """Generate only supported root Terraform resources: private S3 buckets with public-access-block,
versioning and AES256 encryption companions; VPCs, private subnets, security groups, EC2, AWS key pairs,
random_id and tls_private_key.ssh (RSA 4096). Provider versions are pinned by the runner:
aws=5.100.0 random=3.7.2 tls=4.1.0; Terraform=1.17.1. No local provider, provisioners, data sources,
modules, imports, IAM, RDS, user_data, external programs or file-reading functions.
Use a random suffix for unique names. Do not set ACAWorkspace tags: the runner owns those.
S3 force_destroy=false; all four public-access flags=true; versioning=Enabled; encryption=AES256.
Every bucket must have all three companion resources referencing that bucket directly.
EC2: only server-approved literal AMI IDs (ask for configuration if none supplied), instance types
 t3/t4g nano/micro/small, associate_public_ip_address=false, metadata_options.http_tokens=required,
root_block_device.encrypted=true and volume_size between 8 and 30 GiB. Subnets must set
map_public_ip_on_launch=false. Reference resources defined in this config, or IDs explicitly
listed as server-authorized targets. Inventory IDs alone are not authorization. No implicit/default VPC, subnet or security group.
Security group rules: explicit TCP ports and CIDRs; world-open rules only single port 80 or 443;
administrative CIDRs must be explicitly server-authorized. Set ingress=[] and egress=[] when unused.
If an SSH key is needed, create tls_private_key.ssh { algorithm=RSA rsa_bits=4096 } and an aws_key_pair;
do not write a file or output private key material. The runner exports a fixed SSH key artifact.
Return unsupported requests as a clear explanation rather than pretending they are executable.
"""


def validate_terraform_syntax(hcl: str) -> dict:
    """Parse and check static policy only; provider/plan checks run in isolated jobs."""
    from runner.policy import validate_source
    try:
        validate_source(hcl, None)
        return {"valid": True, "message": "Static HCL/policy checks passed; isolated planning is still required."}
    except Exception as error:
        return {"valid": False, "message": str(error) if isinstance(error, ValueError) else "HCL policy validation failed"}


async def generate_terraform_with_anthropic(request: str, api_key: str, existing_infra: str = "", validation_error: str = "") -> dict:
    """Call Anthropic with tool_choice='generate_terraform' to guarantee structured output. The model MUST return a tool_use block; plain text is rejected."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)

    user_content = f"Generate Terraform HCL for: {request}"
    if existing_infra:
        user_content += f"\n\nEXISTING_INFRA:\n{existing_infra}"
    if validation_error:
        user_content += f"\n\nPREVIOUS ATTEMPT FAILED terraform validate — fix ALL these errors:\n{validation_error}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        # model="claude-haiku-20240307",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[TERRAFORM_TOOL],
        tool_choice={"type": "tool", "name": "generate_terraform"},
        messages=[
            {"role": "user", "content": user_content}
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "generate_terraform":
            return block.input

    raise ValueError(
        "Anthropic response contained no generate_terraform tool_use block."
    )


async def generate_terraform_with_groq(request: str, api_key: str, existing_infra: str = "", validation_error: str = "") -> dict:
    """Call Groq using fenced code blocks in the prompt to extract structured HCL and metadata."""
    key = api_key or os.getenv("GROQ_API_KEY", "")
    client = Groq(api_key=key)

    infra_section = f"\n\nEXISTING_INFRA:\n{existing_infra}" if existing_infra else ""
    if validation_error:
        infra_section += f"\n\nPREVIOUS ATTEMPT FAILED terraform validate — fix ALL these errors:\n{validation_error}"

    # We ask for the HCL in a fenced block and the metadata as JSON separately.
    # This avoids the json_object mode failure caused by heavily-escaped HCL strings.
    prompt = f"""{SYSTEM_PROMPT}

Generate Terraform HCL for: {request}{infra_section}

Reply in this exact format — no other text:

```hcl
<complete terraform HCL here>
```

```json
{{
  "resource_type": "<primary AWS resource type, e.g. aws_instance>",
  "description": "<one-sentence plain-English description>"
}}
```"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.1,
    )

    raw = response.choices[0].message.content

    hcl_match = re.search(r"```hcl\s*(.*?)```", raw, re.DOTALL)
    hcl = hcl_match.group(1).strip() if hcl_match else ""

    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        meta = json.loads(json_match.group(1))
    else:
        meta = {"resource_type": "unknown", "description": ""}

    return {
        "hcl": hcl,
        "resource_type": meta.get("resource_type", "unknown"),
        "description": meta.get("description", ""),
    }


async def generate_terraform_with_ollama(request: str, api_key: str, existing_infra: str = "", validation_error: str = "") -> dict:
    """Call a local Ollama instance to generate Terraform HCL. api_key holds the base URL, falling back to localhost."""
    base_url = (api_key or "http://localhost:11434").rstrip("/")

    infra_section = f"\n\nEXISTING_INFRA:\n{existing_infra}" if existing_infra else ""
    if validation_error:
        infra_section += f"\n\nPREVIOUS ATTEMPT FAILED terraform validate — fix ALL these errors:\n{validation_error}"

    prompt = f"""{SYSTEM_PROMPT}

Generate Terraform HCL for: {request}{infra_section}

Reply in this exact format — no other text:

```hcl
<complete terraform HCL here>
```

```json
{{
  "resource_type": "<primary AWS resource type, e.g. aws_instance>",
  "description": "<one-sentence plain-English description>"
}}
```"""

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{base_url}/api/chat",
            json={
                "model": "gpt-oss:120b-cloud",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 4096},
            },
        )
        response.raise_for_status()
        raw = response.json()["message"]["content"]

    hcl_match = re.search(r"```hcl\s*(.*?)```", raw, re.DOTALL)
    hcl = hcl_match.group(1).strip() if hcl_match else ""

    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    meta = json.loads(json_match.group(1)) if json_match else {}

    return {
        "hcl": hcl,
        "resource_type": meta.get("resource_type", "unknown"),
        "description": meta.get("description", ""),
    }


async def generate_terraform(request: str, model: str, api_key: str, existing_infra: str = "") -> dict:
    """Route to the correct LLM provider, validate the generated HCL, and return a structured result dict."""
    try:
        if model == "anthropic":
            result = await generate_terraform_with_anthropic(request, api_key, existing_infra)
        elif model == "ollama":
            result = await generate_terraform_with_ollama(request, api_key, existing_infra)
        else:
            result = await generate_terraform_with_groq(request, api_key, existing_infra)

        validation = validate_terraform_syntax(result["hcl"])

        # Auto-retry once if validation fails — feed the errors back to the LLM so it can fix them.
        if not validation["valid"] and result.get("hcl"):
            if model == "anthropic":
                result = await generate_terraform_with_anthropic(request, api_key, existing_infra, validation["message"])
            elif model == "ollama":
                result = await generate_terraform_with_ollama(request, api_key, existing_infra, validation["message"])
            else:
                result = await generate_terraform_with_groq(request, api_key, existing_infra, validation["message"])
            validation = validate_terraform_syntax(result["hcl"])

        result["validation"] = validation
        result["error"] = None
        return result

    except Exception as e:
        return {
            "hcl": "",
            "resource_type": "unknown",
            "description": "Generation failed.",
            "validation": {"valid": False, "message": str(e)},
            "error": str(e),
        }


def handle_summarise_plan(
    plan_output: str,
    issue_being_fixed: str,
    risk_level: str = "medium",
) -> dict:
    """Parse terraform plan output and return a human-readable approval summary with a safe_to_approve flag."""

    adds = 0
    changes = 0
    destroys = 0

    plan_match = re.search(
        r"Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy",
        plan_output,
        re.IGNORECASE,
    )

    no_changes = bool(
        re.search(
            r"No changes\.|Your infrastructure matches the configuration",
            plan_output,
            re.IGNORECASE,
        )
    )

    if plan_match:
        adds = int(plan_match.group(1))
        changes = int(plan_match.group(2))
        destroys = int(plan_match.group(3))
    elif not no_changes:
        return {
            "summary": (
                f"Could not parse terraform plan output.\n\n"
                f"Issue being addressed: {issue_being_fixed}\n\n"
                f"Raw output (first 500 chars):\n{plan_output[:500]}"
            ),
            "changes_count": 0,
            "risk_level": risk_level,
            "safe_to_approve": False,
        }

    changes_count = adds + changes + destroys

    if destroys > 0:
        risk_level = "high"

    safe_to_approve = (destroys == 0) and (risk_level != "high")

    if no_changes:
        action_line = "No changes will be made to your infrastructure."
    else:
        parts = []
        if adds:
            parts.append(f"{adds} resource{'s' if adds != 1 else ''} will be created")
        if changes:
            parts.append(
                f"{changes} resource{'s' if changes != 1 else ''} will be modified"
            )
        if destroys:
            parts.append(
                f"{destroys} resource{'s' if destroys != 1 else ''} will be DESTROYED"
            )
        action_line = "; ".join(parts) + "."

    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk_level, "⚪")
    approve_text = (
        "This plan appears safe to apply."
        if safe_to_approve
        else "Review carefully before approving — this plan carries elevated risk."
    )

    summary = (
        f"Issue being fixed: {issue_being_fixed}\n\n"
        f"What will happen: {action_line}\n"
        f"  • Resources to create:  {adds}\n"
        f"  • Resources to modify:  {changes}\n"
        f"  • Resources to destroy: {destroys}\n\n"
        f"Risk level: {risk_emoji} {risk_level.upper()}\n\n"
        f"{approve_text}"
    )

    return {
        "summary": summary,
        "changes_count": changes_count,
        "risk_level": risk_level,
        "safe_to_approve": safe_to_approve,
    }


