$ErrorActionPreference = "Stop"

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspaceRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 Python 虚拟环境：$python"
}

Set-Location $workspaceRoot
if (-not $env:DRIVER) {
    $env:DRIVER = "~fastapi+~websockets"
}
& $python -m ff14_app.main
