$ErrorActionPreference = "Stop"

# WebUI 已挂载到统一的 NoneBot 应用；保留此入口避免旧操作手册失效。
& (Join-Path $PSScriptRoot "start-nonebot-dev.ps1")
