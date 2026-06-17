import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

_BACKEND_DIR = Path(__file__).parent.parent
WORKDIR_BASE = _BACKEND_DIR / "terraform_workdirs"
EXECUTION_LOG = _BACKEND_DIR / "execution_log.json"

# Shared provider plugin cache - hardlink to the same binaries instead of new downloads per workdir
PLUGIN_CACHE_DIR = _BACKEND_DIR / "terraform_plugin_cache"

# File-level lock that serialises all read-modify-write operations on the log
_LOG_LOCK = FileLock(str(EXECUTION_LOG) + ".lock", timeout=10)


def _build_env(aws_creds: dict | None) -> dict:
    """Return an os.environ copy with AWS credentials injected if provided."""
    env = os.environ.copy()
    if aws_creds:
        if aws_creds.get("aws_access_key_id"):
            env["AWS_ACCESS_KEY_ID"] = aws_creds["aws_access_key_id"]
        if aws_creds.get("aws_secret_access_key"):
            env["AWS_SECRET_ACCESS_KEY"] = aws_creds["aws_secret_access_key"]
        if aws_creds.get("aws_region"):
            env["AWS_DEFAULT_REGION"] = aws_creds["aws_region"]
    PLUGIN_CACHE_DIR.mkdir(exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(PLUGIN_CACHE_DIR)
    return env


def create_execution_id() -> str:
    """Generate a unique execution ID with a human-readable timestamp prefix."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    short_id = str(uuid.uuid4())[:8]
    return f"exec_{timestamp}_{short_id}"


def get_working_dir(execution_id: str) -> Path:
    """Return (and create if needed) the persistent working directory for a given execution."""
    workdir = WORKDIR_BASE / execution_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def run_terraform_plan(hcl_config: str, execution_id: str, aws_creds: dict | None = None) -> dict:
    """Write HCL to a persistent workdir, then run terraform init and terraform plan. Uses S3 backend if TF_STATE_BUCKET is set, otherwise falls back to local state."""
    try:
        workdir = get_working_dir(execution_id)
        env = _build_env(aws_creds)

        # create workdir and write hcl file
        tf_path = workdir / "main.tf"
        tf_path.write_text(hcl_config, encoding="utf-8")

        # write S3 backend config if configured; fall back to local state if not.
        has_s3_backend = _write_s3_backend_config(workdir, execution_id)
        init_cmd = (
            ["terraform", "init", "-no-color"]
            if has_s3_backend
            else ["terraform", "init", "-backend=false", "-no-color"]
        )

        # delete stale lock file and run terraform init
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

        # runs terraform plan 
        plan_result = subprocess.run(
            ["terraform", "plan", "-out=tfplan", "-no-color"],
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=120,
            env=env,
        )

        plan_output = plan_result.stdout + plan_result.stderr

        # checksum mismatch recovery phase
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


def run_terraform_apply(execution_id: str, aws_creds: dict | None = None) -> dict:
    """Apply the previously planned Terraform execution using the saved tfplan file."""
    try:
        workdir = get_working_dir(execution_id)
        tfplan = workdir / "tfplan"
        env = _build_env(aws_creds)

        # check if tfplan exist
        if not tfplan.exists():
            return {
                "success": False,
                "apply_output": (
                    f"No plan file found for execution '{execution_id}'. "
                    f"Run terraform plan first."
                ),
                "resources_applied": [],
            }

        # run terraform apply with saved tfplan
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

        # .pem key files written by local_file resources on sucess
        key_files = []
        if success:
            for entry in workdir.iterdir():
                if entry.suffix == ".pem" and entry.is_file():
                    key_files.append({"name": entry.name})

        # always clean up provider plugins
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


def run_terraform_destroy(execution_id: str, aws_creds: dict | None = None) -> dict:
    """Destroy resources by re-running terraform init and destroy -auto-approve on the saved main.tf."""
    try:
        workdir = get_working_dir(execution_id)
        tf_path = workdir / "main.tf"
        env = _build_env(aws_creds)

        if not tf_path.exists():
            return {
                "success": False,
                "destroy_output": f"No main.tf found for execution '{execution_id}'. Cannot destroy.",
            }

        backend_tf = workdir / "backend.tf"
        init_cmd = (
            ["terraform", "init", "-no-color"]
            if backend_tf.exists()
            else ["terraform", "init", "-backend=false", "-no-color"]
        )

        # remove stale lock file before init
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

    # resolve path still inside expected dir ensure
    if workdir.resolve() not in key_path.parents:
        raise ValueError("Path traversal detected")

    if not key_path.exists():
        raise FileNotFoundError(f"Key file '{filename}' not found for execution '{execution_id}'")

    return key_path.read_bytes()


def _read_log_unlocked() -> list:
    """Read the log file without acquiring the lock (internal helper)."""
    if not EXECUTION_LOG.exists():
        return []
    try:
        return json.loads(EXECUTION_LOG.read_text(encoding="utf-8"))
    except Exception:
        return []


def log_execution(entry: dict) -> None:
    """Append a new execution entry to the JSON log file."""
    with _LOG_LOCK:
        history = _read_log_unlocked()
        history.append(entry)
        EXECUTION_LOG.write_text(
            json.dumps(history, indent=2, default=str),
            encoding="utf-8",
        )


def log_execution_update(execution_id: str, updates: dict) -> None:
    """Update an existing log entry in-place by execution_id."""
    with _LOG_LOCK:
        history = _read_log_unlocked()
        for entry in history:
            if entry.get("execution_id") == execution_id:
                entry.update(updates)
                break
        EXECUTION_LOG.write_text(
            json.dumps(history, indent=2, default=str),
            encoding="utf-8",
        )


def get_execution_history() -> list:
    """Read and return all execution log entries, or an empty list if the file is missing or corrupt."""
    if not EXECUTION_LOG.exists():
        return []
    try:
        return json.loads(EXECUTION_LOG.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_s3_backend_config(workdir: Path, execution_id: str) -> bool:
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
    cutoff = _time.time() - older_than_days * 86400 # 7 days 
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
