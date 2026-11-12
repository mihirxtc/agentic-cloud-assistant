#!/usr/bin/env bash
# Repeatable host-development setup. Does not start services or change AWS resources.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
interactive=1
case "${1:-}" in
  --non-interactive) interactive=0 ;;
  -h|--help)
    echo 'Usage: ./scripts/setup.sh [--non-interactive]'
    echo 'Install locked dependencies and configure local login without overwriting existing values.'
    exit 0 ;;
  '') ;;
  *) echo 'Usage: ./scripts/setup.sh [--non-interactive]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Unexpected arguments.' >&2; exit 2; }
for tool in uv node npm; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Install it, then rerun setup." >&2; exit 1; }
done
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 12)) { console.error("Use Node 22.12 or newer (CI uses Node 22)."); process.exit(1) }'
cd "$ROOT"
if [[ -e .venv || -L .venv ]]; then
  [[ -x .venv/bin/python ]] || { echo 'Existing .venv is incomplete; move it aside deliberately before rerunning setup.' >&2; exit 1; }
else
  echo 'Creating a Python 3.11 environment with uv…'
  uv venv --python 3.11 "$ROOT/.venv"
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else "Existing .venv must use Python 3.11; it has not been replaced.")'
echo 'Installing locked backend dependencies (includes PyTorch and embeddings)…'
uv pip install --python "$ROOT/.venv/bin/python" -r backend/requirements.txt -r backend/requirements-test.txt
uv pip check --python "$ROOT/.venv/bin/python"
echo 'Installing frontend dependencies…'
npm --prefix "$ROOT/frontend" ci
if (( interactive )); then
  .venv/bin/python scripts/dev_support.py configure
else
  .venv/bin/python scripts/dev_support.py configure --non-interactive
fi
printf '\nSetup finished. Configure AWS/model access in backend/.env, then run:\n  ./scripts/dev.sh\n'
