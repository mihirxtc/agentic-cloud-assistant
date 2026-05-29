import json
import os

import httpx
from dotenv import load_dotenv

from ollama_catalog import DEFAULT_MODEL

load_dotenv()


async def chat_with_groq(
    message: str, scan_data: dict, history: list = None, api_key: str = None
) -> str:
    """Send a message to the Groq API with AWS scan data injected as system context."""

    if history is None:
        history = []

    if api_key and api_key.strip():
        resolved_key = api_key.strip()
    else:
        resolved_key = os.getenv("GROQ_API_KEY")

    if not resolved_key:
        return (
            "No Groq API key provided. Please add GROQ_API_KEY to .env "
            "or enter your key in the chat interface."
        )

    system_prompt = (
        "You are a friendly AWS cloud management assistant. "
        "Answer the user's questions in plain, conversational English. "
        "You have been given live data about their AWS account — use it to give accurate, specific answers.\n\n"
        "AWS ACCOUNT DATA:\n"
        f"{json.dumps(scan_data, indent=2, default=str)}\n\n"
        "DATA GUIDE — key fields to know:\n"
        "- sg_usage.sg_usage: maps every security group ID to a list of attached resources. "
        "An empty list means that security group is unused and safe to review for deletion.\n"
        "- sg_usage.unused_sg_ids: pre-computed list of security group IDs with zero attached resources.\n"
        "- sg_usage.unused_count: total number of unused security groups.\n"
        "- ec2.instances[].security_group_ids: which SGs each EC2 instance uses.\n\n"
        "STRICT RULES:\n"
        "- Respond ONLY in natural English sentences — never paste JSON, code blocks, or raw data into your reply\n"
        "- Be specific: mention counts, resource IDs, and names by extracting them from the data above\n"
        "- Be concise — 1 to 4 sentences unless the user asks for detail\n"
        "- If the data does not contain what they asked, say so plainly\n"
        "- Never say 'According to the data' or quote field names like 'count' or 'status'"
    )

    clean_history = [
        {"role": m["role"], "content": m.get("content") or m.get("text", "")}
        for m in history
        if m.get("role") in ("user", "assistant")
    ]

    messages = [{"role": "system", "content": system_prompt}]
    messages = messages + clean_history
    messages.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {resolved_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1024,
                },
            )

            data = response.json()

            if response.status_code != 200:
                error_message = data.get("error", {}).get("message", str(data))
                return f"Groq API error ({response.status_code}): {error_message}"

            return data["choices"][0]["message"]["content"]

    except Exception as e:
        return f"Error contacting Groq: {str(e)}"


async def chat_with_ollama(
    message: str,
    scan_data: dict,
    history: list = None,
    api_key: str = None,
    model_name: str = DEFAULT_MODEL,
) -> str:
    """Send a message to a local Ollama instance with AWS scan data injected as system context."""

    if history is None:
        history = []

    ec2 = scan_data.get("ec2", {})
    s3 = scan_data.get("s3", {})
    iam = scan_data.get("iam", {})
    sgs = scan_data.get("security_groups", {})
    vpc = scan_data.get("vpc", {})

    ec2_summary = [
        {
            "id": i.get("id") or i.get("instance_id"),
            "name": i.get("name"),
            "type": i.get("type") or i.get("instance_type"),
            "state": i.get("state"),
            "public_ip": i.get("public_ip"),
            "private_ip": i.get("private_ip"),
            "launch_time": i.get("launch_time"),
            "security_groups": i.get("security_group_ids", []),
        }
        for i in ec2.get("instances", [])
    ]

    s3_summary = [
        {
            "name": b.get("name"),
            "is_public": b.get("is_public"),
            "created": b.get("created"),
        }
        for b in s3.get("buckets", [])
    ]

    iam_summary = [
        {
            "username": u.get("username"),
            "has_mfa": u.get("has_mfa"),
            "last_login": u.get("last_login"),
            "groups": u.get("groups", []),
            "attached_policies": [p["name"] if isinstance(p, dict) else p for p in u.get("attached_policies", [])],
            "inline_policies": u.get("inline_policies", []),
            "access_keys": u.get("access_keys", []),
        }
        for u in iam.get("users", [])
    ]

    sg_summary = [
        {
            "id": sg.get("id") or sg.get("group_id"),
            "name": sg.get("name") or sg.get("group_name"),
            "vpc_id": sg.get("vpc_id"),
            "is_dangerous": sg.get("is_dangerous"),
            "open_ports": sg.get("open_to_internet", []),
        }
        for sg in sgs.get("security_groups", [])
    ]

    vpc_summary = [
        {
            "id": v.get("id") or v.get("vpc_id"),
            "name": v.get("name"),
            "cidr": v.get("cidr") or v.get("cidr_block"),
            "is_default": v.get("is_default"),
            "state": v.get("state"),
            "subnet_count": v.get("subnet_count"),
        }
        for v in vpc.get("vpcs", [])
    ]

    sg_usage_data = scan_data.get("sg_usage", {})

    compact_summary = {
        "ec2_count": ec2.get("count", 0),
        "ec2_instances": ec2_summary,
        "s3_count": s3.get("count", 0),
        "s3_buckets": s3_summary,
        "iam_user_count": iam.get("user_count", 0),
        "iam_users": iam_summary,
        "security_group_count": sgs.get("count", 0),
        "dangerous_sg_count": sum(
            1 for sg in sgs.get("security_groups", []) if sg.get("is_dangerous")
        ),
        "security_groups": sg_summary,
        "vpc_count": vpc.get("count", 0),
        "vpcs": vpc_summary,
        "sg_usage": sg_usage_data,
    }

    system_prompt = (
        "You are a friendly AWS cloud management assistant. "
        "Answer the user's questions in plain, conversational English. "
        "You have been given live data about their AWS account — use it to give accurate, specific answers.\n\n"
        "AWS ACCOUNT DATA:\n"
        f"{json.dumps(compact_summary, indent=2, default=str)}\n\n"
        "DATA GUIDE — key fields to know:\n"
        "- sg_usage.sg_usage: maps every security group ID to a list of attached resources. "
        "An empty list means that security group is unused and safe to review for deletion.\n"
        "- sg_usage.unused_sg_ids: pre-computed list of security group IDs with zero attached resources.\n"
        "- sg_usage.unused_count: total number of unused security groups.\n\n"
        "STRICT RULES:\n"
        "- Respond ONLY in natural English sentences — never paste JSON, code blocks, or raw data into your reply\n"
        "- Be specific: mention counts, resource IDs, and names by extracting them from the data above\n"
        "- Be concise — 1 to 4 sentences unless the user asks for detail\n"
        "- If the data does not contain what they asked, say so plainly\n"
        "- Never say 'According to the data' or quote field names like 'count' or 'status'"
    )

    clean_history = [
        {"role": m["role"], "content": m.get("content") or m.get("text", "")}
        for m in history
        if m.get("role") in ("user", "assistant")
    ]

    messages = [{"role": "system", "content": system_prompt}]
    messages = messages + clean_history
    messages.append({"role": "user", "content": message})

    try:
        base_url = (api_key or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model_name,
                    "messages": messages,
                    "stream": False,
                },
            )

            if response.status_code != 200:
                return f"Ollama API error ({response.status_code}): {response.text}"

            data = response.json()

            return data["message"]["content"]

    except httpx.ConnectError:
        return (
            f"Cannot connect to Ollama. Make sure Ollama is running "
            f"(run: ollama serve) and {model_name} is installed "
            f"(run: ollama pull {model_name})"
        )

    except httpx.ReadTimeout:
        return (
            "Ollama timed out (180s). The model is likely still loading "
            "into memory. Wait 30 seconds and try again."
        )

    except Exception as e:
        return f"Ollama error: {type(e).__name__}: {str(e)}"


async def prompt_llm(
    prompt: str,
    model: str = "groq",
    api_key: str = "",
    model_name: str = DEFAULT_MODEL,
) -> str:
    """One-shot prompt with no system prompt or scan data; intended for summarisation and RAG answer generation."""
    key = api_key.strip() if api_key else None

    if model == "anthropic":
        resolved = key or os.getenv("ANTHROPIC_API_KEY", "")
        if not resolved:
            return "No Anthropic API key available."
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": resolved,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1024,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                if resp.status_code != 200:
                    return f"Anthropic error: {data.get('error', {}).get('message', str(data))}"
                return data["content"][0]["text"]
        except Exception as e:
            return f"Error contacting Anthropic: {str(e)}"

    elif model == "ollama":
        base_url = (key or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{base_url}/api/chat",
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    },
                )
                if resp.status_code != 200:
                    return f"Ollama error ({resp.status_code}): {resp.text}"
                return resp.json()["message"]["content"]
        except httpx.ConnectError:
            return "Cannot connect to Ollama. Make sure Ollama is running (run: ollama serve)."
        except Exception as e:
            return f"Ollama error: {str(e)}"

    else:  # groq (default)
        resolved = key or os.getenv("GROQ_API_KEY", "")
        if not resolved:
            return "No Groq API key available."
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {resolved}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 1024,
                    },
                )
                data = resp.json()
                if resp.status_code != 200:
                    return f"Groq error: {data.get('error', {}).get('message', str(data))}"
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Error contacting Groq: {str(e)}"


async def chat_with_anthropic(
    message: str, scan_data: dict, history: list = None, api_key: str = None
) -> str:
    """Send a message to the Anthropic API with AWS scan data as context.

    Unlike Groq/Ollama, the system prompt goes in a top-level 'system' field, not inside the messages array.
    """

    if history is None:
        history = []

    if api_key and api_key.strip():
        resolved_key = api_key.strip()
    else:
        resolved_key = os.getenv("ANTHROPIC_API_KEY")

    if not resolved_key:
        return (
            "No Anthropic API key provided. Please add ANTHROPIC_API_KEY "
            "to .env or enter your key in the chat interface."
        )

    system_prompt = (
        "You are a friendly AWS cloud management assistant. "
        "Answer the user's questions in plain, conversational English. "
        "You have been given live data about their AWS account — use it to give accurate, specific answers.\n\n"
        "AWS ACCOUNT DATA:\n"
        f"{json.dumps(scan_data, indent=2, default=str)}\n\n"
        "DATA GUIDE — key fields to know:\n"
        "- sg_usage.sg_usage: maps every security group ID to a list of attached resources. "
        "An empty list means that security group is unused and safe to review for deletion.\n"
        "- sg_usage.unused_sg_ids: pre-computed list of security group IDs with zero attached resources.\n"
        "- sg_usage.unused_count: total number of unused security groups.\n"
        "- ec2.instances[].security_group_ids: which SGs each EC2 instance uses.\n\n"
        "STRICT RULES:\n"
        "- Respond ONLY in natural English sentences — never paste JSON, code blocks, or raw data into your reply\n"
        "- Be specific: mention counts, resource IDs, and names by extracting them from the data above\n"
        "- Be concise — 1 to 4 sentences unless the user asks for detail\n"
        "- If the data does not contain what they asked, say so plainly\n"
        "- Never say 'According to the data' or quote field names like 'count' or 'status'"
    )

    clean_history = [
        {"role": m["role"], "content": m.get("content") or m.get("text", "")}
        for m in history
        if m.get("role") in ("user", "assistant")
    ]

    messages = list(clean_history)
    messages.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": resolved_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": messages,
                },
            )

            data = response.json()

            if response.status_code != 200:
                error_message = data.get("error", {}).get("message", str(data))
                return f"Anthropic API error ({response.status_code}): {error_message}"

            return data["content"][0]["text"]

    except Exception as e:
        return f"Error contacting Anthropic: {str(e)}"
