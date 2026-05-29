import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import anthropic
import httpx
from groq import Groq

# Shared plugin cache for the validate subprocess — same directory as used by
# execution_service, so providers downloaded during plan/apply are reused here.
_PLUGIN_CACHE_DIR = Path(__file__).parent.parent / "terraform_plugin_cache"

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
                "description": "Complete Terraform HCL including terraform{} with hashicorp/aws ~>5.0 AND hashicorp/random ~>3.0 providers, a random_id suffix resource, provider{}, variable{}, and all resource blocks. Every AWS resource name MUST include ${random_id.suffix.hex} to guarantee uniqueness.",
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


SYSTEM_PROMPT = """\
You are a Terraform expert that generates clean, valid AWS HCL configurations.

NAMING UNIQUENESS — CRITICAL:
Every config MUST include the hashicorp/random provider and a random_id resource so that
resource names are unique across multiple deployments of the same template:

  terraform {
    required_providers {
      aws    = { source = "hashicorp/aws",    version = "~> 5.0" }
      random = { source = "hashicorp/random", version = "~> 3.0" }
    }
  }

  resource "random_id" "suffix" { byte_length = 4 }

Append ${random_id.suffix.hex} to resource names that must be GLOBALLY UNIQUE in AWS
(security group names within a VPC, S3 bucket names, IAM entity names, DB identifiers,
key pair names). EC2 Name TAGS do not need the suffix — use the user's requested name
exactly as the Name tag value.
  tags = { Name = "mihir-server-1" }          # exact user-specified name in tag
  name = "demo-sg-${random_id.suffix.hex}"    # suffix only on names that must be unique
This prevents 400/409 "already exists" conflicts on every re-deploy.

EC2 SSH KEY PAIR — MANDATORY for EVERY aws_instance, no exceptions:
Even if the user does not mention SSH, keys, or port 22 — you MUST always generate a TLS
key pair and save the private key as a .pem file so the user can connect to their instance.
Use the tls and local providers in required_providers for every EC2 config:

  terraform {
    required_providers {
      aws    = { source = "hashicorp/aws",    version = "~> 5.0" }
      random = { source = "hashicorp/random", version = "~> 3.0" }
      tls    = { source = "hashicorp/tls",    version = "~> 4.0" }
      local  = { source = "hashicorp/local",  version = "~> 2.0" }
    }
  }

  resource "tls_private_key" "ssh" {
    algorithm = "RSA"
    rsa_bits  = 4096
  }

  resource "aws_key_pair" "this" {
    key_name   = "${var.name_prefix}-key-${random_id.suffix.hex}"
    public_key = tls_private_key.ssh.public_key_openssh
  }

  resource "local_file" "private_key" {
    content         = tls_private_key.ssh.private_key_pem
    filename        = "${path.module}/${var.name_prefix}-key.pem"
    file_permission = "0400"
  }

  # ALWAYS attach the key pair to the instance:
  resource "aws_instance" "this" {
    key_name = aws_key_pair.this.key_name
    ...
  }

  # ALWAYS add port 22 ingress to the security group and an SSH output:
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  output "ssh_command" {
    value = "ssh -i ${var.name_prefix}-key.pem ec2-user@${aws_instance.this.public_ip}"
  }

NEW EC2 INSTANCE WITH SSH — when creating a fresh EC2 instance with a NEW security group:
  Use a variable for the SSH CIDR with default "0.0.0.0/0" so the instance is reachable,
  AND always emit an output block reminding the user to lock it down:

  variable "allowed_ssh_cidr" {
    description = "CIDR allowed to SSH. Default 0.0.0.0/0 works for demos — restrict to your IP (run: curl ifconfig.me) for production."
    default     = "0.0.0.0/0"
  }

  # In the security group ingress rule:
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  # Always add this output so the user sees the security reminder in plan/apply output:
  output "ssh_security_reminder" {
    value = "SSH is open to ${var.allowed_ssh_cidr}. To restrict: set allowed_ssh_cidr=<your-ip>/32 (get your IP: curl ifconfig.me)"
  }

  203.0.113.45/32 is an RFC 5737 documentation-only IP that belongs to nobody — NEVER use it
  for new EC2 instances because it will block all real SSH connections.

MY IP / SPECIFIC IP RESTRICTION — ONLY when the user says "from my IP" or "my IP only"
AND you are adding a rule to a PRE-EXISTING security group (not a newly created one):
Use a variable with a documentation-range default so terraform plan succeeds AND the new
rule is never a duplicate of the existing 0.0.0.0/0 rule already on that security group:
  variable "allowed_ssh_cidr" {
    description = "CIDR for SSH access. REPLACE with your actual IP before applying, e.g. 203.0.113.45/32"
    default     = "203.0.113.45/32"
  }
Do NOT use default = "0.0.0.0/0" in this specific case — it duplicates the existing open
rule on the pre-existing SG and causes AWS to reject the apply with "duplicate Security
Group rule" errors. Use ${var.allowed_ssh_cidr} in the ingress rule.

Additional rules:
- Always include a provider "aws" {} block with a variable "region" (default = "us-east-1")
- Use a variable "name_prefix" whose default is the explicit server/resource name from the user's request. A name is ONLY when the user writes something like "my-server", "prod-api", "mihirxtc-test-1", "fedoraisgreat" — a human-chosen identifier. Resource type descriptions such as "EC2 t3.micro", "S3 bucket with versioning", "VPC with public + private subnets", "IAM role for EC2" are NOT names. If the user provides no explicit name, always set default = "aca".
- Use descriptive, lowercase resource labels (e.g. "main", "this")
- Do NOT include backend configuration
- Do NOT hardcode AWS credentials or account IDs
- Keep the config minimal but complete and immediately usable
- NEVER use data sources that query AWS at plan time (no data "aws_availability_zones",
  data "aws_vpc", data "aws_subnets", data "aws_ami", etc.) — hardcode sensible defaults instead.
  EXCEPTION — AMI IDs: ALWAYS use the SSM Parameter Store data source to fetch the latest
  Amazon Linux 2023 AMI instead of hardcoding an AMI ID (hardcoded IDs get deregistered by AWS
  and cause "couldn't find resource" errors on apply). Required pattern for every EC2 instance:

    data "aws_ssm_parameter" "al2023_ami" {
      name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
    }

    resource "aws_instance" "this" {
      ami           = data.aws_ssm_parameter.al2023_ami.value
      instance_type = "t3.micro"
      ...
    }

  This data source is safe: terraform validate (run without credentials) skips it,
  and terraform plan/apply (run with credentials) resolves it correctly.
- For availability zones use literal strings: "${var.region}a" and "${var.region}b"
- For EC2 security groups omit vpc_id to use the default VPC
- For RDS: always create an aws_vpc + two aws_subnet resources (10.0.1.0/24 in <region>a,
  10.0.2.0/24 in <region>b) and an aws_db_subnet_group referencing them inline
- For VPC configs: hardcode CIDR blocks (10.0.0.0/16, subnets 10.0.x.0/24) and AZ literals
- For IAM: use aws_iam_policy_document data source (it is local computation, not an AWS API call)
- For S3 bucket names: AWS requires globally unique names — the random suffix is mandatory
- For EBS storage: use root_block_device { volume_size = N, volume_type = "gp3" } inside the
  aws_instance resource for the root volume. For additional volumes use aws_ebs_volume +
  aws_volume_attachment.
- EC2 OUTPUT ATTRIBUTE NAMES — CRITICAL: aws_instance exports these exact names:
    public_ip        (NOT public_ip_address)
    private_ip       (NOT private_ip_address)
    public_dns       (NOT public_dns_name)
    private_dns      (NOT private_dns_name)
    id               (NOT instance_id)
  Using wrong names causes "Unsupported attribute" errors from terraform validate.

SECURITY GROUP RULE FIXES — CRITICAL:
- `revoke_rules` is an argument on `aws_security_group` ONLY — NEVER place it inside
  an `aws_security_group_rule` resource; it will cause "Unsupported argument" errors
- To restrict SSH/port access on an existing security group: create a NEW
  `aws_security_group_rule` resource with the restricted cidr_blocks and type = "ingress"
- Never attempt to destroy or modify individual existing rules via Terraform without
  first importing them — instead add the restrictive rule and note the open rule must
  be removed manually
- Correct pattern for SSH restriction fix:
    resource "aws_security_group_rule" "restrict_ssh" {
      type              = "ingress"
      from_port         = 22
      to_port           = 22
      protocol          = "tcp"
      cidr_blocks       = [var.allowed_ssh_cidr]   # must NOT be 0.0.0.0/0
      security_group_id = "<existing-sg-id>"
    }
- The new rule CIDR must differ from the existing open rule — using 0.0.0.0/0 would
  duplicate the existing rule and cause AWS to reject with "duplicate Security Group rule"
- Always use the RFC 5737 TEST-NET default (203.0.113.45/32) FOR SECURITY GROUP FIXES
  so plan + apply succeed; the user then replaces it with their real IP
- IMPORTANT: 203.0.113.45/32 is ONLY correct here (fixing an existing SG). For new EC2
  instances use 0.0.0.0/0 so the instance is actually reachable.

EXISTING INFRASTRUCTURE AWARENESS — CRITICAL:
When the user message contains an EXISTING_INFRA block, you MUST read it carefully and:
1. If a suitable security group already exists, reference its ID as a literal string instead
   of creating a new aws_security_group resource.
   Example: vpc_security_group_ids = ["sg-0abc123def456789a"]
2. If a VPC already exists, use its ID as a literal string for vpc_id instead of creating
   a new aws_vpc.
   Example: vpc_id = "vpc-0abc123def456789a"
3. If subnets already exist in the right AZs, use their IDs as literal strings instead of
   creating new aws_subnet resources.
   Example: subnet_ids = ["subnet-0abc123", "subnet-0def456"]
4. If an IAM role already exists for the needed purpose, reference it by ARN or name.
5. NEVER create an S3 bucket with the same name as one listed in EXISTING_INFRA.
6. Only create new resources when nothing suitable already exists.

VPC COMPATIBILITY — CRITICAL when using EXISTING_INFRA:
The subnet_id and every ID in vpc_security_group_ids MUST belong to the same VPC.
Each entry in EXISTING_INFRA shows its vpc= field — always check they match before
using them together. Mixing a subnet from vpc-AAA with a security group from vpc-BBB
causes AWS to reject the RunInstances call with an opaque error.
If no matching subnet exists in the same VPC as your chosen security group, omit
subnet_id entirely and let AWS place the instance in the default subnet.\
"""


def validate_terraform_syntax(hcl: str) -> dict:
    """Run terraform init and terraform validate in a temp directory; return {"valid": bool, "message": str}."""
    # Pass the shared plugin cache so providers are reused across validate calls
    # instead of being re-downloaded from scratch on every invocation.
    _PLUGIN_CACHE_DIR.mkdir(exist_ok=True)
    cache_env = os.environ.copy()
    cache_env["TF_PLUGIN_CACHE_DIR"] = str(_PLUGIN_CACHE_DIR)

    with tempfile.TemporaryDirectory() as tmpdir:
        tf_path = os.path.join(tmpdir, "main.tf")
        with open(tf_path, "w") as f:
            f.write(hcl)

        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-no-color"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=120,
            env=cache_env,
        )
        if init.returncode != 0:
            return {
                "valid": False,
                "message": f"Init failed: {(init.stderr or init.stdout).strip()}",
            }

        validate = subprocess.run(
            ["terraform", "validate", "-no-color"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
            env=cache_env,
        )
        if validate.returncode == 0:
            return {"valid": True, "message": "Terraform configuration is valid."}

        return {
            "valid": False,
            "message": (validate.stderr or validate.stdout).strip(),
        }


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


