#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$project_root/.env" ]]; then
  set -a
  source "$project_root/.env"
  set +a
fi
cd "$project_root/backend"

# Homebrew Node may run under Rosetta and make a universal Python select x86_64,
# even when this venv contains arm64 wheels. Pin Python to the native Apple
# Silicon architecture when the machine supports it.
python_cmd=(../.venv/bin/python)
if [[ "$(uname -s)" == "Darwin" ]] && [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  python_cmd=(/usr/bin/arch -arm64 ../.venv/bin/python)
fi

exec "${python_cmd[@]}" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
