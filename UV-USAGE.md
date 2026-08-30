# UV 使用约束

状态：当前 NoneBot2 应用运行说明，更新于 2026-08-26。当前发行版本为 `0.2.0`，发行限制见 [`CHANGELOG.md`](CHANGELOG.md)。

`.venv` 由 UV 按 `pyproject.toml` 和 `uv.lock` 管理。项目只使用独立应用、NoneBot2、OneBot V11 Adapter、FastAPI 和开发工具所需依赖。

## 常用命令

```powershell
.\.venv\Scripts\uv.exe lock
.\.venv\Scripts\uv.exe lock --check
.\.venv\Scripts\uv.exe pip check
.\.venv\Scripts\uv.exe run --locked python -m unittest discover -s tests -v
```

修改 `pyproject.toml` 后执行：

```powershell
.\.venv\Scripts\uv.exe lock
.\.venv\Scripts\uv.exe sync --locked
```

不要在应用运行时同步依赖，以免替换已加载的模块。锁文件必须与项目声明一致，环境中不得存在已删除的 QQ 网关包。

应用启动时会执行可重复的 SQLite schema 初始化；版本变化会在 `data/ff14_bot.sqlite3`
旁生成迁移前备份，并启用 WAL。不要通过删除数据库来处理迁移错误。

## 统一运行入口

将 `.env.example` 复制为被忽略的本机 `.env`，填写 NapCat 正向 WebSocket 地址和 token 后运行。Bot 所有者与服务群白名单在 WebUI 的“群授权”页面管理：

```powershell
.\scripts\start-nonebot-dev.ps1
```

使用 `ONEBOT_V11_WS_URLS=[]` 可以在无 NapCat 时验证 NoneBot 生命周期和插件加载；连接真实 NapCat 前必须确认同一测试群没有另一套网关处理事件。

WebUI 已挂载在同一进程：`http://127.0.0.1:20036/`，群授权页为 `/access`。不要再单独启动 WebUI；旧的 `start-webui.ps1` 只是调用统一入口的兼容包装器。
