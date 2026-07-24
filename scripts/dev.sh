#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if [[ ! -x ".venv/bin/uvicorn" ]]; then
  echo "后端依赖尚未安装，请先按照 README 完成初始化。" >&2
  exit 1
fi

bash scripts/dev-backend.sh &
backend_pid=$!

cleanup() {
  kill "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm run dev
