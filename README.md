# FFXIVBot

面向少量指定 QQ 群的独立 FF14 信息助手，使用 Python、NoneBot2、OneBot V11 和 SQLite，通过既有 NapCat 接入 QQ。

当前代码版本为 `0.3.0`：支持国服与 NA 官方资讯、活动摘要、新公告及维护提醒，通过本机 WebUI 管理群授权和订阅。市场查询仍在规划中；已验证能力与剩余限制以 [TODO.md](TODO.md) 为准。

## 获取代码

```bash
git clone https://github.com/FreezeNovo/FFXIVBot.git
cd FFXIVBot
```

开发和离线基线验证使用 Linux `.venv-linux`，环境恢复步骤见 [UV-USAGE.md](UV-USAGE.md#linux-前置条件与环境恢复)。完成依赖安装后运行：

```bash
bash scripts/check-linux.sh
```

实际部署目标为 Windows 服务器，操作与待验收项见 [Windows 部署指南](WINDOWS-DEPLOYMENT.md)；解压部署包的入口见 [快速开始](WINDOWS-QUICKSTART.md)。

## 开发与运行边界

GitHub 用于托管源码、锁文件、测试和项目文档。开发平台暂未配置；本地 `.env`、`config.yaml`、数据库、日志、虚拟环境和构建产物均不上传。

离线测试使用固定 fixture 和临时数据库。正常启动入口会运行来源轮询与投递调度；即使禁用 QQ 连接，也不能将其当作离线检查命令。真实连接、消息发送和正式数据库升级需按 [开发约束](AGENTS.md) 单独授权。

## 项目文档

| 内容 | 入口 |
| --- | --- |
| 产品范围和已确认规则 | [GOAL.md](GOAL.md) |
| 架构与模块边界 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 实现进度与已知限制 | [TODO.md](TODO.md) |
| 开发流程与验收约束 | [AGENTS.md](AGENTS.md) |
| 环境、检查与运行命令 | [UV-USAGE.md](UV-USAGE.md) |
| 本机管理界面 | [WEBUI.md](WEBUI.md) |
| 上游来源契约 | [DATA-SOURCES.md](DATA-SOURCES.md) |
| 发行记录 | [CHANGELOG.md](CHANGELOG.md) |
| 本地 Markdown 任务跟踪 | [任务跟踪约定](docs/agents/issue-tracker.md) |
