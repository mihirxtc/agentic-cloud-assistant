import os
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from services.storage import get_execution_store
from services.aws_connection import get_connection, require_record_connection, AWSConnection, AWSIdentityError
from services.aws_terraform_guard import validate_aws_configuration

_BACKEND_DIR = Path(__file__).parent.parent
WORKDIR_BASE = _BACKEND_DIR / "terraform_workdirs"

# Shared provider plugin cache — all executions hardlink to the same binaries
# instead of downloading ~727 MB per workdir. On the same filesystem, Terraform
# uses hardlinks so disk usage is the size of one copy (~727 MB total), not
# 727 MB × number of executions.
PLUGIN_CACHE_DIR = _BACKEND_DIR / "terraform_plugin_cache"


def _build_env(connection: AWSConnection) -> dict:
    """Use the exact credentials verified by STS, including temporary tokens."""
    env = os.environ.copy()
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
                "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        env.pop(key, None)
    env.update(AWS_ACCESS_KEY_ID=connection.access_key, AWS_SECRET_ACCESS_KEY=connection.secret_key,
               AWS_REGION=connection.region, AWS_DEFAULT_REGION=connection.region,
               AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
               AWS_EC2_METADATA_DISABLED="true", TF_VAR_region=connection.region)
    if connection.token:
        env["AWS_SESSION_TOKEN"] = connection.token
    PLUGIN_CACHE_DIR.mkdir(exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(PLUGIN_CACHE_DIR)
    return env


def _provider_guard(connection):
    return {"provider": {"aws": [{"region": connection.region, "allowed_account_ids": [connection.account_id]}]}}


def _check_provider_guard(workdir, connection):
    guard = workdir / "aca_identity_override.tf.json"
    if not guard.exists() or json.loads(guard.read_text()) != _provider_guard(connection):
        raise AWSIdentityError("The saved Terraform account/region guard is missing or changed. Create a new reviewed plan.")


def create_execution_id() -> str:
    """Generate a unique execution ID with a human-readable timestamp prefix."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    short_id = str(uuid.uuid4())[:8]
    return f"exec_{timestamp}_{short_id}"


def get_working_dir(execution_id: str) -> Path:
    """Return (and create if needed) the persistent working directory for a given execution."""
    if not re.fullmatch(r"exec_\d{8}_\d{6}_[a-f0-9]{8}", execution_id):
        raise ValueError("Invalid execution ID")
    workdir = WORKDIR_BASE / execution_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def run_terraform_plan(hcl_config: str, execution_id: str) -> dict:
    """Write HCL to a persistent workdir, then run terraform init and terraform plan. Uses S3 backend if TF_STATE_BUCKET is set, otherwise falls back to local state."""
    try:
        connection = get_connection()
        validate_aws_configuration(hcl_config, connection)
        workdir = get_working_dir(execution_id)
        env = _build_env(connection)

        tf_path = workdir / "main.tf"
        tf_path.write_text(hcl_config, encoding="utf-8")
        (workdir / "aca_identity_override.tf.json").write_text(json.dumps(_provider_guard(connection)), encoding="utf-8")

        # Write S3 backend config if configured; fall back to local state if not.
        has_s3_backend = _write_s3_backend_config(workdir, execution_id, connection)
        init_cmd = (
            ["terraform", "init", "-no-color"]
            if has_s3_backend
            else ["terraform", "init", "-backend=false", "-no-color"]
        )

        # Remove any stale lock file so init always regenerates it from the current
        # plugin cache. A leftover lock file with old checksums causes "Required plugins
        # are not installed" even though the providers are physically present.
        (workdir / ".terraform.lock.hcl").unlink(missing_ok=True)

        init_result = subprocess.run(
            init_cmd,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=120,
            env=env,
        )

        if init_result.returncode != 0:
            init_output = init_result.stdout + init_result.stderr
            return {
                "success": False,
                "plan_output": f"terraform init failed:\n{init_output.strip()}",
                "resources_to_add": 0,
                "resources_to_change": 0,
                "resources_to_destroy": 0,
            }

        plan_result = subprocess.run(
            ["terraform", "plan", "-out=tfplan", "-no-color"],
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=120,
            env=env,
        )

        plan_output = plan_result.stdout + plan_result.stderr

        # Provider checksum mismatch: cached binary diverged from registry hashes.
        # Clear the stale provider from the shared cache, wipe .terraform/, and retry
        # init+plan once so the next download is authoritative.
        if plan_result.returncode != 0 and "does not match any of the checksums" in plan_output:
            shutil.rmtree(PLUGIN_CACHE_DIR, ignore_errors=True)
            PLUGIN_CACHE_DIR.mkdir(exist_ok=True)
            env["TF_PLUGIN_CACHE_DIR"] = str(PLUGIN_CACHE_DIR)
            shutil.rmtree(workdir / ".terraform", ignore_errors=True)
            (workdir / ".terraform.lock.hcl").unlink(missing_ok=True)
            subprocess.run(init_cmd, capture_output=True, text=True, cwd=workdir, timeout=120, env=env)
            plan_result = subprocess.run(
                ["terraform", "plan", "-out=tfplan", "-no-color"],
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=120,
                env=env,
            )
            plan_output = plan_result.stdout + plan_result.stderr

        return {
            "success": plan_result.returncode == 0,
            "plan_output": plan_output,
            "resources_to_add": _parse_count(plan_output, "add"),
            "resources_to_change": _parse_count(plan_output, "change"),
            "resources_to_destroy": _parse_count(plan_output, "destroy"),
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "plan_output": "terraform plan timed out after 120 seconds.",
            "resources_to_add": 0,
            "resources_to_change": 0,
            "resources_to_destroy": 0,
        }
    except Exception as e:
        return {
            "success": False,
            "plan_output": f"Unexpected error during plan: {str(e)}",
            "resources_to_add": 0,
            "resources_to_change": 0,
            "resources_to_destroy": 0,
        }


def run_terraform_apply(execution_id: str) -> dict:
    """Apply the previously planned Terraform execution using the saved tfplan file."""
    try:
        connection = require_record_connection(execution_id)
        workdir = get_working_dir(execution_id)
        _check_provider_guard(workdir, connection)
        validate_aws_configuration((workdir / "main.tf").read_text(), connection)
        tfplan = workdir / "tfplan"
        env = _build_env(connection)

        if not tfplan.exists():
            return {
                "success": False,
                "apply_output": (
                    f"No plan file found for execution '{execution_id}'. "
                    f"Run terraform plan first."
                ),
                "resources_applied": [],
            }

        apply_result = subprocess.run(
            ["terraform", "apply", "-auto-approve", "-no-color", "tfplan"],
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=300,
            env=env,
        )

        apply_output = apply_result.stdout + apply_result.stderr
        success = apply_result.returncode == 0

        # Collect .pem key files written by local_file resources (only on success).
        key_files = []
        if success:
            for entry in workdir.iterdir():
                if entry.suffix == ".pem" and entry.is_file():
                    key_files.append({"name": entry.name})

        # Always clean up provider plugins and the consumed plan binary —
        # regardless of success/failure. Previously this was inside `if success:`
        # which left ~727 MB of provider binaries stranded on failed applies.
        # main.tf and terraform.tfstate are kept: main.tf for rollback,
        # tfstate because Terraform needs it to track created resources.
        _cleanup_workdir_plugins(workdir)

        return {
            "success": success,
            "apply_output": apply_output,
            "resources_applied": _parse_applied(apply_output),
            "key_files": key_files,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "apply_output": "terraform apply timed out after 300 seconds.",
            "resources_applied": [],
        }
    except Exception as e:
        return {
            "success": False,
            "apply_output": f"Unexpected error during apply: {str(e)}",
            "resources_applied": [],
        }


def run_terraform_destroy(execution_id: str) -> dict:
    """Destroy resources by re-running terraform init and destroy -auto-approve on the saved main.tf."""
    try:
        connection = require_record_connection(execution_id)
        workdir = get_working_dir(execution_id)
        _check_provider_guard(workdir, connection)
        validate_aws_configuration((workdir / "main.tf").read_text(), connection)
        tf_path = workdir / "main.tf"
        env = _build_env(connection)

        if not tf_path.exists():
            return {
                "success": False,
                "destroy_output": f"No main.tf found for execution '{execution_id}'. Cannot destroy.",
            }

        # If backend.tf is present the plan used S3 state — init without -backend=false
        # so Terraform fetches state from S3. Otherwise use local -backend=false mode.
        backend_tf = workdir / "backend.tf"
        init_cmd = (
            ["terraform", "init", "-no-color"]
            if backend_tf.exists()
            else ["terraform", "init", "-backend=false", "-no-color"]
        )

        # Remove stale lock file for the same reason as in run_terraform_plan.
        (workdir / ".terraform.lock.hcl").unlink(missing_ok=True)

        init_result = subprocess.run(
            init_cmd,
            capture_output=True, text=True, cwd=workdir, timeout=120, env=env,
        )
        if init_result.returncode != 0:
            return {
                "success": False,
                "destroy_output": f"terraform init failed:\n{(init_result.stdout + init_result.stderr).strip()}",
            }

        destroy_result = subprocess.run(
            ["terraform", "destroy", "-auto-approve", "-no-color"],
            capture_output=True, text=True, cwd=workdir, timeout=300, env=env,
        )
        output  = destroy_result.stdout + destroy_result.stderr
        success = destroy_result.returncode == 0

        _cleanup_workdir_plugins(workdir)

        return {"success": success, "destroy_output": output}

    except subprocess.TimeoutExpired:
        return {"success": False, "destroy_output": "terraform destroy timed out after 300 seconds."}
    except Exception as e:
        return {"success": False, "destroy_output": f"Unexpected error during destroy: {str(e)}"}


def get_key_file(execution_id: str, filename: str) -> bytes:
    """Read and return the raw bytes of a .pem key file. Raises ValueError on path-traversal attempts, non-.pem filenames, or paths resolving outside the execution workdir."""
    if "/" in filename or "\\" in filename:
        raise ValueError(f"Invalid filename: '{filename}'")
    if not filename.endswith(".pem"):
        raise ValueError("Only .pem files can be downloaded")

    workdir = WORKDIR_BASE / execution_id
    key_path = (workdir / filename).resolve()

    # Ensure the resolved path is still inside the expected workdir
    if workdir.resolve() not in key_path.parents:
        raise ValueError("Path traversal detected")

    if not key_path.exists():
        raise FileNotFoundError(f"Key file '{filename}' not found for execution '{execution_id}'")

    return key_path.read_bytes()


def log_execution(entry: dict) -> None:
    """Append an execution to the persistent transactional store."""
    get_execution_store().append(entry)


def log_execution_update(execution_id: str, updates: dict) -> None:
    """Update an existing execution atomically; unknown IDs raise KeyError."""
    get_execution_store().update(execution_id, updates)


def get_execution_history() -> list:
    """Return persisted history; storage errors propagate instead of hiding data."""
    return get_execution_store().history()


def _write_s3_backend_config(workdir: Path, execution_id: str, connection: AWSConnection) -> bool:
    """Write backend.tf into workdir for S3 state if TF_STATE_BUCKET is set; return True if written, False if absent."""
    bucket = os.getenv("TF_STATE_BUCKET", "").strip()
    if not bucket:
        return False

    region = os.getenv("TF_STATE_REGION", "us-east-1").strip()

    backend_hcl = (
        'terraform {\n'
        '  backend "s3" {\n'
        f'    bucket       = "{bucket}"\n'
        f'    key          = "{execution_id}/terraform.tfstate"\n'
        f'    region       = "{region}"\n'
        '    use_lockfile = true\n'
        '    encrypt      = true\n'
        f'    allowed_account_ids = ["{connection.account_id}"]\n'
        '  }\n'
        '}\n'
    )
    (workdir / "backend.tf").write_text(backend_hcl, encoding="utf-8")
    return True


def _cleanup_workdir_plugins(workdir: Path) -> None:
    """Delete .terraform/ and tfplan from a workdir. Internal — always safe to call."""
    plugin_dir = workdir / ".terraform"
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir, ignore_errors=True)
    tfplan_file = workdir / "tfplan"
    if tfplan_file.exists():
        tfplan_file.unlink(missing_ok=True)


def cleanup_workdir_plugins(execution_id: str) -> None:
    """Remove provider plugins and plan binary for a rejected or abandoned execution."""
    workdir = WORKDIR_BASE / execution_id
    if workdir.is_dir():
        _cleanup_workdir_plugins(workdir)


def purge_old_workdirs(older_than_days: int = 7) -> dict:
    """Delete workdirs without a terraform.tfstate that are older than the threshold. Applied workdirs are skipped to preserve rollback capability."""
    import time as _time
    cutoff = _time.time() - older_than_days * 86400
    removed = 0
    freed = 0
    if not WORKDIR_BASE.exists():
        return {"removed": 0, "freed_bytes": 0}
    for workdir in WORKDIR_BASE.iterdir():
        if not workdir.is_dir():
            continue
        if (workdir / "terraform.tfstate").exists():
            continue  # keep applied workdirs — rollback may still be needed
        if workdir.stat().st_mtime < cutoff:
            size = sum(f.stat().st_size for f in workdir.rglob("*") if f.is_file())
            shutil.rmtree(workdir, ignore_errors=True)
            removed += 1
            freed += size
    return {"removed": removed, "freed_bytes": freed}


def _parse_count(output: str, action: str) -> int:
    """Parse a resource change count for 'add', 'change', or 'destroy' from terraform plan output."""
    patterns = {
        "add": r"(\d+) to add",
        "change": r"(\d+) to change",
        "destroy": r"(\d+) to destroy",
    }
    match = re.search(patterns.get(action, ""), output)
    return int(match.group(1)) if match else 0


def _parse_applied(output: str) -> list:
    """Extract resource addresses from terraform apply output."""
    return re.findall(r"([\w.]+): Creation complete", output)
