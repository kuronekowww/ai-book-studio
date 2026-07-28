#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：bash scripts/revalidate-partial-chapters.sh <book_id>" >&2
  exit 2
fi

project_root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$project_root/.env" ]]; then
  set -a
  source "$project_root/.env"
  set +a
fi
cd "$project_root/backend"

python_cmd=(../.venv/bin/python)
if [[ "$(uname -s)" == "Darwin" ]] && [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  python_cmd=(/usr/bin/arch -arm64 ../.venv/bin/python)
fi

exec "${python_cmd[@]}" -m app.maintenance revalidate-partial --book-id "$1"
