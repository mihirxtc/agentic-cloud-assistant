"""Bounded no-follow artifact access relative to trusted directory descriptors."""
import hashlib
import os
from pathlib import Path
import re
import stat

ID_PATTERN = r'exec_\d{8}_\d{6}_[a-f0-9]{8}'
MAX_FILE = 64 * 1024 * 1024


def workspace_id(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise ValueError('Invalid workspace ID')
    return value


def artifact_name(value):
    if value not in ('main.tf', 'aca_identity_override.tf.json', 'aca_policy_override.tf.json', 'aca_backend.tf.json',
                     '.terraform.lock.hcl', 'tfplan', 'terraform.tfstate', 'ssh-key.pem') and not re.fullmatch(r'destroy_' + ID_PATTERN + r'\.tfplan', str(value)):
        raise ValueError('Artifact is not permitted')
    return value


def directory_fd(path):
    # The root itself must be deployment-controlled, never a caller-chosen path.
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def read_file(root, name, limit=MAX_FILE):
    artifact_name(name)
    parent = directory_fd(root)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError('Unsafe or oversized artifact')
            chunks, size = [], 0
            while chunk := os.read(descriptor, min(65536, limit + 1 - size)):
                chunks.append(chunk); size += len(chunk)
                if size > limit:
                    raise ValueError('Artifact exceeds size limit')
            return b''.join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def write_file(root, name, content):
    artifact_name(name)
    if len(content) > MAX_FILE:
        raise ValueError('Artifact exceeds size limit')
    parent = directory_fd(root)
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, 'wb', closefd=False) as output:
                output.write(content); output.flush(); os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def workspace(root, identifier, create=False):
    workspace_id(identifier)
    root = Path(root)
    parent = directory_fd(root)
    try:
        if create:
            os.mkdir(identifier, mode=0o700, dir_fd=parent)
        descriptor = os.open(identifier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        os.close(descriptor)
    finally:
        os.close(parent)
    return root / identifier


def manifest(root, plan_file):
    artifact_name(plan_file)
    names = {'main.tf', 'aca_identity_override.tf.json', 'aca_policy_override.tf.json', '.terraform.lock.hcl', plan_file}
    if (Path(root) / 'aca_backend.tf.json').exists():
        names.add('aca_backend.tf.json')
    # Reject additional configuration rather than silently leaving it outside the digest.
    for path in Path(root).iterdir():
        if path.name.endswith(('.tf', '.tf.json', '.tfvars', '.tfvars.json')) and path.name not in names:
            raise ValueError('Unrecognized Terraform input')
    return {name: hashlib.sha256(read_file(root, name)).hexdigest() for name in sorted(names)}
