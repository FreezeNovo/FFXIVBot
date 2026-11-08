# Windows 服务器部署指南

状态：2026-11-09，适用于当前 `0.3.0` / schema `22`。开发与离线基线在 Linux；实际部署目标为 Windows 服务器。目标 LTSC 临时目录的依赖同步、完整离线测试和系统锁已实测；首次工具下载、UAC、真实应用启动、开机自启及长期运行仍待验收，详见 [快速开始](WINDOWS-QUICKSTART.md)。

本文只提供操作步骤，不表示已授权执行生产迁移或真实群发送。当前实现与历史验收见 [TODO.md](TODO.md)，迁移语义见 [UV-USAGE.md](UV-USAGE.md#p3-schema-22-升级与恢复)，功能管理见 [WEBUI.md](WEBUI.md)。

2026-11-08 已提供可解压部署的在线初始化 ZIP。优先使用 [解压包快速开始](WINDOWS-QUICKSTART.md)：双击根目录 `Start-FFXIVBot.cmd` 自动检查/安装依赖并进入启动预检；使用当前 Windows 用户，仅目录权限修复按需申请 UAC。下文手工步骤供排错和运维使用。

## 1. 部署前准备

- 使用受维护的 Windows Server x64 系统和 PowerShell；本文路径统一示例为 `C:\Apps\FFXIVBot`，请按实际路径替换。不同 Windows Server 版本尚未逐一验证。
- 默认使用当前 Windows 运行用户，让它拥有项目目录、Python 安装目录的读取/执行权限，以及 `data` 的写入权限；只有运行账户和维护者能读取 `.env`、数据库、日志及备份。不要求应用以管理员身份运行。
- 服务器能够访问依赖下载站、国服官网及 NA Lodestone HTTPS 来源；能连接既有 NapCat 的正向 OneBot V11 WebSocket。
- NapCat 已由其维护者准备好。本项目不安装、登录、重配或停止 NapCat，也不修改既有 Koishi。
- 确认旧 Linux/Windows FFXIVBot 已停止，且没有另一套 QQ 网关处理同一目标群。不同目录、不同机器之间的进程锁不互通，不能靠它阻止跨机器双发。
- WebUI 固定监听 `127.0.0.1:20036`；无需开放入站防火墙端口。通过服务器远程桌面上的浏览器管理，不将 WebUI 转发为公网服务。

无需 Node.js、Docker、WSL 或独立数据库服务。SQLite 与时区数据由 Python/锁定依赖提供。NapCat 属于非官方 QQ 接入方式，存在平台限制或封号风险，项目不承诺消除该风险。

## 2. 放置代码并重建 Windows 环境

将选定发行版本的完整项目代码放入 `C:\Apps\FFXIVBot`，包含 `pyproject.toml`、`uv.lock`、`ff14_app`、`ff14_core`、`ff14_web`、`plugins`、`tests`、`scripts` 和测试 fixtures。记录部署版本或提交号；不要直接从正在改动的开发目录覆盖生产目录。

**不复制 Linux `.venv-linux` 或旧 Windows `.venv`。** 虚拟环境包含本机路径和平台二进制，应在服务器新建。历史 `.venv` 曾因解释器原路径失效而无法运行；如果目标目录已有环境，停应用后将其另行留存，再创建新环境，不直接删除。

使用将来运行任务的同一账户安装 uv。使用解压包启动器时无需手工执行本节安装，安装位置见 [快速开始](WINDOWS-QUICKSTART.md#当前用户与-uac)。以下为 [uv 官方 Windows 安装方式](https://docs.astral.sh/uv/getting-started/installation/)（2026-11-06 核验）；组织禁止执行远程安装脚本时，按该页面下载独立发行包。

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

重新打开 PowerShell，执行：

```powershell
Set-Location 'C:\Apps\FFXIVBot'
$ErrorActionPreference = 'Stop'
$env:UV_PROJECT_ENVIRONMENT = '.venv'
uv --version
uv python install 3.14.7
if ($LASTEXITCODE -ne 0) { throw 'Python 安装失败' }
uv sync --locked --python 3.14.7
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
.\.venv\Scripts\python.exe --version
```

项目声明 Python `>=3.12`；这里使用2026-11-09在目标LTSC完成离线检查的 `3.14.7` 作为手动操作示例。升级时显式选择并记录新的完整补丁版本，然后重新完成下一节检查；不要改回仅指定 `3.14`，否则uv可能使用本机拒绝访问的minor版本联接。自动启动器会从本机实际安装目录探测完整版本，无需将示例版本固化为产品要求。指定 Python 的安装行为见 [uv 官方说明](https://docs.astral.sh/uv/guides/install-python/)（2026-11-09核验）。

使用默认开发依赖组是为了安装锁定的 `uv==0.12.4`，供后续检查；不要执行 `uv lock` 更新生产锁文件，也不要在应用运行期间同步依赖。

## 3. 先做离线检查

在项目根目录逐项执行，任何一项失败都先解决再继续：

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv'
.\.venv\Scripts\uv.exe lock --check --offline --no-python-downloads
if ($LASTEXITCODE -ne 0) { throw '锁文件检查失败' }
.\.venv\Scripts\uv.exe pip check --python .\.venv\Scripts\python.exe
if ($LASTEXITCODE -ne 0) { throw '依赖兼容检查失败' }
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '离线测试失败，停止部署' }
.\.venv\Scripts\python.exe -c "import ssl, sqlite3; from zoneinfo import ZoneInfo; print(ssl.OPENSSL_VERSION, sqlite3.sqlite_version, ZoneInfo('Asia/Shanghai'))"
if ($LASTEXITCODE -ne 0) { throw '基础运行环境检查失败' }
```

测试使用临时数据库和固定 fixture，不应连接真实 NapCat 或发送消息。全套测试包含子进程争锁及临时生命周期检查；Windows 失败须保留输出并定位，不能以 Linux 通过替代 Windows 验收。

不要用 `python -m ff14_app.main` 做离线检查：即使 `ONEBOT_V11_WS_URLS=[]`，正常入口仍会初始化/迁移数据库、抓取上游并启动投递任务。

## 4. 选择全新部署或迁移已有状态

### 全新部署

不放置旧数据库，首次启动会创建 `data/ff14_bot.sqlite3`。所有者和群策略在 WebUI 配置。此路线不保留旧投递历史，不能把已有生产实例伪装成全新部署来绕过迁移。

### 从 Linux 或旧 Windows 实例迁移

SQLite 文件可以随应用状态迁移；不要迁移虚拟环境。先停止旧 FFXIVBot 并暂停其开机自启，确认进程已退出，再制作冷备：

1. 将完整 `data/`、`.env`、已有 `config.yaml`（如有）、原代码版本及锁文件备份到独立目录。备份包含账号、策略和消息历史，按私密数据保存。
2. 从已停止的实例复制整个 `data/` 到新服务器；如存在 `-wal`/`-shm`，一并保留。不要从运行中的 WAL 数据库只复制 `.sqlite3` 主文件。也不要拼接不同备份中的主库与 WAL/SHM。
3. 不迁移 `runtime.lock` 文件也可以，启动会创建；锁文件存在不等于进程正在运行。绝不能通过删除正在使用的锁来绕过保护。
4. **首次正常启动前**，在新服务器预演。下面的目标文件和时间戳目录必须是新建的：

```powershell
Set-Location 'C:\Apps\FFXIVBot'
$backupStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$previewDir = Join-Path 'data\backups' "windows-preview-$backupStamp"
New-Item -ItemType Directory -Path $previewDir -ErrorAction Stop | Out-Null
.\.venv\Scripts\python.exe -m ff14_app.migration_preview --source data/ff14_bot.sqlite3 --destination "$previewDir\preview.sqlite3"
if ($LASTEXITCODE -ne 0) { throw '副本迁移失败，禁止启动正式应用' }
```

该命令只读复制源库，仅升级独立副本并检查完整性/外键；不加载 `.env`、不启动调度、不联网。当前预期 `preview_schema: 22`，核对原 schema 及各投递状态数量；无法解释的差异先停止部署。原库迁移会在下节正常启动时发生，先确认冷备可用。

16→22 的保留规则、旧活动核实要求和版本化备份名称统一见 [迁移说明](UV-USAGE.md#p3-schema-22-升级与恢复)。迁移不会替你关闭已有群策略：旧库一旦连接 NapCat，可能立即进行正常投递或有效窗口内的恢复处理。

## 5. 配置连接并首次启动

没有 `.env` 时才复制模板，避免覆盖已有凭据：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
notepad .env
```

保留模板的 `DRIVER=~fastapi+~websockets`、`HOST=127.0.0.1`、`PORT=20036`；填写既有 NapCat 的 `ONEBOT_V11_WS_URLS` JSON 数组和 `ONEBOT_V11_ACCESS_TOKEN`。同机地址使用既有服务的回环地址与实际端口；跨机器使用可信内网/VPN地址及 ACL。这里只连接已有服务，不提供修改 NapCat 的步骤。

首次检查迁移和 WebUI 时，可临时设 `ONEBOT_V11_WS_URLS=[]`，避免建立 QQ 连接；这仍是会写库、会访问上游的正常启动。环境变量可能覆盖 `.env`，还应检查当前账户没有遗留的同名连接变量。不要输出或分享完整环境变量、`.env` 和连接日志。

在已完成冷备/预演并确认启动影响后运行：

```powershell
Set-Location 'C:\Apps\FFXIVBot'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-nonebot-dev.ps1
```

脚本名字保留 `dev`，实际调用同一个 `ff14_app.main`，没有热重载。若企业策略禁止 Bypass，可在根目录直接执行 `.\.venv\Scripts\python.exe -m ff14_app.main`，由 `.env` 提供驱动配置。

在服务器浏览器打开 [本机 WebUI](http://127.0.0.1:20036/)，依次检查：

1. `/access`：填写 Bot 所有者、添加精确服务群。白名单不等于订阅；普通群员活动/维护查询分别授权。
2. `/news` 与 `/access`：检查每群公告、活动摘要、维护提醒的独立开关、区域、分类和日程；迁移已有库时先核对所有旧策略。
3. `/settings`、`/sources`、`/articles`：检查运行参数、来源抓取和活动核实结果。日程按产品定义的时区处理，不靠修改 Windows 本地时区纠正；服务器系统时间应准确。
4. `/deliveries`：检查历史和未知结果，未知结果先在群中核实，再选择关闭记录，不自动重发。

如先禁用了 QQ 连接，检查完用 `Ctrl+C` 正常停止，再填写真实连接并启动。启用真实连接前确认获准的目标群、消息流和测试范围；不拿其他群做试发。测试群内验证所有者/普通群员权限、消息收件及重启不重复，随后再逐步启用生产策略。已订阅公告的新启用规则见 [产品说明](GOAL.md#1-官方资讯订阅)。

## 6. 用任务计划程序实现开机自启

先完成前台验收并停止前台进程，再创建任务。任务只启动 FFXIVBot，NapCat 的可用性由其既有运维方式保证。

在 Windows“任务计划程序”中选择“创建任务”：

| 页面 | 配置 |
| --- | --- |
| 常规 | 名称 `FFXIVBot`；选择前面安装 Python/uv 的当前用户；选择“不管用户是否登录都要运行”，按系统提示保存账户凭据。无需默认勾选最高权限。 |
| 触发器 | “启动时”，可延迟 1 分钟给网络恢复留出时间。 |
| 操作：程序 | `C:\Apps\FFXIVBot\.venv\Scripts\python.exe` |
| 操作：参数 | `-m ff14_app.main` |
| 操作：起始于 | `C:\Apps\FFXIVBot`，不要留空；否则可能无法导入模块或读取 `.env`。 |
| 条件 | 服务器长期运行时，避免电池/空闲等条件意外停止任务。根据服务器策略配置。 |
| 设置 | 允许按需运行；失败后每 1 分钟重试、最多 3 次；已运行时“不启动新实例”；取消“运行超过指定时间则停止”。 |

这里直接运行 Python，让任务计划程序观察实际应用退出码。脚本入口和任务计划程序必须使用同一个目录/环境，不能各启动一份。重试次数有限，连续失败后需维护者检查；它不提供应用卡死检测或完整监控。

Windows 任务默认有运行时长限制，长期 Bot 必须取消该限制；依据 [Microsoft 执行时限说明](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-executiontimelimit)及[任务设置文档](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps)（2026-11-06 核验）。

创建后手动运行一次，检查 WebUI 和 `data/logs/p3.log`，再安排服务器重启演练，确认未登录桌面时也能启动。任务持续运行时显示“正在运行”是预期；任务成功启动不等于 NapCat 已在线或所有来源抓取成功。后台任务不保存前台控制台输出；P3 诊断日志有界轮转，但启动早期失败仍可能需要停任务后前台复现。日志范围和限制见 [诊断说明](UV-USAGE.md#p3-运行参数与诊断)。

## 7. 停止、更新与恢复

前台运行优先 `Ctrl+C` 并等待退出。计划维护时先禁用计划任务以防重启，再停止该任务并确认 Python 进程退出；任务计划程序的“结束”可能是强制中断，应检查投递中的未知结果。不要结束其他 Python 程序，也不需要停止 NapCat/Koishi。

更新顺序：停应用和自动启动 → 冷备完整状态及原代码 → 放置指定版本代码 → `uv sync --locked`（沿用验收的 Python）→ 离线全套检查 → 新路径副本迁移预演 → 启动核验 → 恢复自动启动。正式运行不执行自动拉取最新代码、自动改锁或无人值守 schema 升级。

日常安排定期备份；当前没有内置自动备份调度。最简单的可靠方式是在维护窗口停应用后备份完整 `data/`、`.env` 及代码版本清单到受限的独立存储，并在独立目录演练恢复。运行中备份必须使用 SQLite Backup API 等一致性备份方式，不能只按文件复制主库。迁移自动备份不能代替日常备份。

恢复前停应用并禁用自启，保存故障库及其 WAL/SHM。把完整备份放到新的独立恢复目录，不与故障库混用；使用匹配的代码和重建的 Windows 环境。仅验证恢复库时，在该目录执行：

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from ff14_core.storage import StateStore; StateStore(Path('data/ff14_bot.sqlite3')).validate_integrity(); print('integrity: PASS')"
if ($LASTEXITCODE -ne 0) { throw '恢复库校验失败' }
```

若还需升级，先对恢复库做独立副本预演。代码回退必须配套旧 schema 的备份，不能把 schema22 库交给旧版本。恢复旧账本会丢失备份后已发送记录，可能造成重复消息；恢复后先保持 QQ 连接禁用，核查备份后的真实消息与策略，再决定重新上线。

## 8. 常见故障与上线检查

| 现象 | 先检查 |
| --- | --- |
| 找不到 Python 或入口无法运行 | 是否复制了旧 `.venv`、移动了解释器；用运行账户在服务器重新建立环境。 |
| uv trampoline os2，或Python联接被报不受信任装入点os448 | 使用2026-11-09修正版启动器，自动从实际版本目录探测解释器、显式请求完整补丁版本；保留旧环境后重建，不关闭系统安全保护。 |
| 提示无法取得运行锁 | 查找同目录已有实例和计划任务；不要删除锁文件绕过。另一个目录的重复实例需单独检查。 |
| WebUI 无法打开 | 是否在服务器本机浏览器访问；进程是否仍在；20036 是否被占用；是否存在覆盖端口的环境变量。不要改成 `0.0.0.0` 排错。 |
| WebUI 正常但 QQ 离线 | 检查既有 NapCat 在线状态、正向 WS 地址/token和网络可达性；不要把token贴到工单。 |
| 没有主动消息 | 检查群启用和各流策略、时间/区域、来源新鲜度及投递状态；合法空结果和过旧暂停都可能不发送。 |
| 手动启动正常，后台任务失败 | 运行账户、凭据、Python路径、项目“起始于”目录，以及账户访问内网/代理的权限。 |
| 几天后自动退出 | 任务运行时长限制、系统休眠/更新和任务历史；检查重试耗尽后的根因。 |
| 升级或测试失败 | 停止上线，保留错误和副本；不得删库、修改生产锁文件或跳过失败测试。 |

上线前应记录 Windows版本、代码提交、Python/uv版本、离线测试结果、备份/预演路径，并完成本机监听、真实目标范围、重启/离线重连、单实例保护和消息去重验收。随后观察至少一个实际日程周期以及超过默认任务时限的持续运行；没有完成的项目保留为待验收，不能把本文当成 Windows 已验证通过的证据。
