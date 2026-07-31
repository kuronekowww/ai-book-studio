#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if [[ "$(uname -s)" == "Darwin" ]] && [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  if [[ ! -x "/opt/homebrew/bin/node" ]]; then
    echo "未找到 Apple Silicon 原生 Node：/opt/homebrew/bin/node" >&2
    echo "请先通过 Homebrew 安装 Node，再重新运行 npm run studio。" >&2
    exit 1
  fi
  export PATH="/opt/homebrew/bin:$PATH"
  export AI_BOOK_STUDIO_REQUIRE_ARM64=1
fi

node scripts/check-node-runtime.mjs

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
