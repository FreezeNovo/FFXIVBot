# FFXIVBot 迁移与清理记录

状态：NoneBot2 迁移已完成，更新于 2026-08-24。实施顺序见 [`TODO.md`](TODO.md)，目标边界见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 当前运行边界

FFXIVBot 是独立 Python 应用，QQ 接入唯一使用本地 `plugins/qq_nonebot` 网关、NoneBot2、OneBot V11 Adapter 和既有 NapCat 正向 WebSocket。Core、FastAPI、调度和 SQLite 不依赖 QQ 框架，也不作为通用 NoneBot 商店插件发布。

```text
NapCat -> NoneBot2 + FastAPI ASGI -> qq_nonebot -> Core / SQLite
                              -> WebUI (/、/access)
```

统一入口为 `scripts/start-nonebot-dev.ps1`；WebUI、Core、来源轮询、活动摘要和国服维护提醒调度与 NoneBot 在同一进程中启动和停止。

旧 QQ 网关已彻底移除，不保留兼容入口或回退运行时。NapCat 仍是外部 provider，本项目不安装、登录、重配或停止 NapCat。

## 已完成的清理

- 从 `pyproject.toml` 移除旧网关依赖，并重新生成 `uv.lock`。
- 删除旧网关插件、manifest、启动脚本和 `config.yaml`（其中包含本地连接凭据）。
- 删除旧运行时生成的 QQ 状态、日志和 Python 缓存；保留 `data/ff14_bot.sqlite3`、新闻快照、活动数据和测试 fixture。
- 连接地址和 token 从被忽略的 `.env` 读取；Bot 所有者与服务群白名单由本机 WebUI 写入 SQLite。
- Core 只通过 `CommandRequest`、`OutboundMessage` 和 `MessagingPort` 与网关交互。

## 验收边界

2026-08-24 已在测试群人工验证分类候选、WebUI 官方详情链接，以及使用同一 query/renderer
的活动摘要真实定时消息。国服维护自动投递目前由离线回归测试覆盖，尚待专用测试群人工验收。
断线、长期运行和重复进程等边界仍按下方验收范围持续观察。

迁移后的验收应覆盖 NoneBot 插件加载、无 NapCat 生命周期烟测、专用测试群的白名单和私聊静默、断线/离线状态、回复与主动投递失败，以及进程重启后的投递幂等。NapCat 广播的同一事件只能由一套网关处理。

旧 Koishi 部署和归档的 `koishi-plugin-ff14calendar` 不属于迁移输入、运行时或兼容目标。它们的历史边界见 [`ARCHIVE-KOISHI.md`](ARCHIVE-KOISHI.md)。
