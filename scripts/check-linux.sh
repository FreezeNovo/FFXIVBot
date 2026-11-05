#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
project_python="$workspace_root/.venv-linux/bin/python"
project_uv="$workspace_root/.venv-linux/bin/uv"
if [[ ! -x "$project_python" || ! -x "$project_uv" ]]; then
    echo "未找到完整 Linux 开发环境；请先按 UV-USAGE.md 恢复 .venv-linux。" >&2
    exit 1
fi

cd -- "$workspace_root"
export UV_PROJECT_ENVIRONMENT="$workspace_root/.venv-linux"
"$project_uv" lock --check --offline --no-python-downloads
"$project_uv" pip check --python "$project_python"
"$project_python" -m unittest discover -s tests -v
