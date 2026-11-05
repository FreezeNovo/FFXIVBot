#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
project_python="$workspace_root/.venv-linux/bin/python"

if [[ ! -x "$project_python" ]]; then
    echo "未找到 Linux Python 环境；请先按 UV-USAGE.md 同步 .venv-linux。" >&2
    exit 1
fi

cd -- "$workspace_root"
export DRIVER="${DRIVER:-~fastapi+~websockets}"
# WebUI 尚无认证，只允许本机访问。
export HOST=127.0.0.1
export PORT="${PORT:-20036}"
exec "$project_python" -m ff14_app.main
