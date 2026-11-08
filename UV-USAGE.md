# UV 使用约束

状态：当前 NoneBot2 应用运行说明，更新于 2026-11-05。当前代码版本为 `0.3.0`，发行限制见 [`CHANGELOG.md`](CHANGELOG.md)，恢复开发核查见 [`TODO.md`](TODO.md)。

虚拟环境由 UV 按 `pyproject.toml` 和 `uv.lock` 管理。2026-11-02 起，当前工作区开发与离线基线检查使用 Linux `.venv-linux`。2026-11-06 确认实际部署目标为 Windows 服务器，按 [`WINDOWS-DEPLOYMENT.md`](WINDOWS-DEPLOYMENT.md) 在目标机器独立重建 `.venv`；当前工作区的旧 Windows 环境不删除或覆盖，也不能视为可用部署环境。两个环境均不进入版本控制，不能跨平台复制。

## Linux 前置条件与环境恢复

2026-10-30 在 CachyOS x86_64 上核验：系统已有 Python 3.14.8、uv 0.12.1、Git、可用的 Python SSL/SQLite 模块和时区数据库。项目声明 Python `>=3.12`；本次实际验证的是 3.14.8，不代表所有声明版本均已测试。

无需安装 Node.js/npm、PowerShell、独立数据库服务器或浏览器自动化运行时。本机锁定依赖均可直接安装，无需补装 C/Rust 编译工具。FastAPI、NoneBot2、OneBot V11、WebSocket、时区及其依赖由锁文件统一安装；开发环境会同时安装锁定的 uv 0.12.4，无需替换系统 uv。

在仓库根目录执行（`/usr/bin/python3` 是本机已核验的解释器路径）：

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv sync --locked --python /usr/bin/python3 --no-python-downloads --link-mode copy
UV_PROJECT_ENVIRONMENT=.venv-linux .venv-linux/bin/uv lock --check --offline --no-python-downloads
.venv-linux/bin/uv pip check --python .venv-linux/bin/python
.venv-linux/bin/python -m unittest discover -s tests -v
```

安装依赖需要网络或完整缓存；测试本身使用离线 fixture 和临时数据库。当前工作区与 uv 缓存位于不同文件系统，`--link-mode copy` 避免硬链接回退提示，不改变依赖版本。

Linux 下运行 `uv sync` / `uv run` 时显式指定 `UV_PROJECT_ENVIRONMENT=.venv-linux`，避免触碰 Windows `.venv`。自定义环境目录的机制见 [uv 官方文档](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)（2026-10-30 核验）。Windows 虚拟环境中的解释器、路径和原生扩展不能直接用于 Linux。

日常离线校验统一执行 `bash scripts/check-linux.sh`，包含锁文件检查、依赖兼容检查和完整测试。脚本固定使用 `.venv-linux`，不启动应用。直接运行 Python 时使用 `.venv-linux/bin/python`；启动使用 `bash scripts/start-nonebot-dev.sh`。

## Windows 服务器环境与检查

解压部署包可普通双击 `Start-FFXIVBot.cmd`，自动安装当前用户范围的 uv/Python 和锁定依赖，执行检查并在确认后启动。`Start-FFXIVBot.cmd -CheckOnly` 只准备环境和验证，不启动业务任务。配置、按需 UAC 与备份预演见 [快速开始](WINDOWS-QUICKSTART.md)；打包命令为 `.venv-linux/bin/python scripts/build-windows-package.py --output /tmp/FFXIVBot-0.3.0-windows.zip`，输出须尚不存在，不打包本地状态。

安装、环境重建、完整离线检查及开机自启见 [`WINDOWS-DEPLOYMENT.md`](WINDOWS-DEPLOYMENT.md)。2026-11-09修正版已在目标LTSC临时目录通过依赖同步与151项离线测试；不代表真实应用已上线。启动器使用已探测到的完整Python补丁版本创建环境，避免uv minor版本联接被系统拒绝。以下命令在服务器项目根目录执行，当前 Linux 开发入口保持不变：

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv'
.\.venv\Scripts\uv.exe lock --check
.\.venv\Scripts\uv.exe pip check --python .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

开发中有意修改 `pyproject.toml` 后才更新锁文件；生产部署仅同步已有锁文件，不执行 `lock` 更新版本：

```powershell
.\.venv\Scripts\uv.exe lock
.\.venv\Scripts\uv.exe sync --locked
```

不要在应用运行时同步依赖，以免替换已加载的模块。锁文件必须与项目声明一致，环境中不得存在已删除的 QQ 网关包。

应用启动时会执行可重复的 SQLite schema 初始化；版本变化会在 `data/ff14_bot.sqlite3`
旁生成迁移前备份，并启用 WAL。不要通过删除数据库来处理迁移错误。

## schema 16 升级与恢复

历史修复使用 schema `16`；当前P3代码的升级说明见下节。本机数据库已于 2026-11-02 经备份和副本预演后完成升级，验证记录见 [`TODO.md`](TODO.md#linux-开发切换与正式数据库升级2026-11-02)。以下步骤保留用于后续恢复或其他副本升级。升级前停止 FFXIVBot，保留 `.env`、原代码版本和现有备份；不需要停止 NapCat 或 Koishi。

先在副本上预演（仓库根目录执行，目标文件必须不存在，路径可替换为自己的本机备份位置）：

```bash
.venv-linux/bin/python -m ff14_app.migration_preview --source data/ff14_bot.sqlite3 --destination data/migration-preview.sqlite3
```

Windows 将解释器替换为 `.\.venv\Scripts\python.exe`，其余参数相同。预演用只读连接通过 SQLite Backup API 复制源库，仅迁移目标副本，检查完整性和外键并输出 schema 与各投递状态数量，不加载 `.env`、不联网或启动调度。目标副本及其备份含本地业务数据，应与原库同样保管。预演失败时保留副本供检查；重新预演请换一个不存在的目标路径。

确认副本结果后，正常启动会自动迁移原库。迁移会保留来源快照、群策略、通知和 attempts，并产生以下变化：

- 旧 `pending` 若已有发送尝试，保守标为 `uncertain`，避免把旧超时结果当成未发送；没有尝试的旧队列标为 `superseded`，不会在升级后直接补发。它不关闭将来的正常调度。
- 普通群员活动/维护查询授权默认关闭，请在 `/access` 按群显式开启；主动推送原有策略保留。
- 候选和尝试不再随群删除级联消失；移除群改为归档。
- 拒绝打开来自更高 schema 的数据库，避免自动降级。

自动备份位于数据库旁：`.pre-migration.bak` 是最近一次迁移尝试前副本；`.pre-v15-to-v16.bak` 是首次从 15 升级到 16 前的版本化副本，重试不覆盖。若原库版本不同，文件名中的 15 随原版本变化。不要在失败重试后误把最近副本当作最初备份。

恢复时先停止应用并保留失败库及其 `-wal`/`-shm` 文件供排查；将版本化备份复制到一个新的空恢复目录，先运行 `StateStore(恢复路径).validate_integrity()` 验证。需要回退代码时必须配套使用旧 schema 的备份；不要把 schema 16 库交给旧代码。确认后再把恢复副本放回运行路径，不可遗留原库的 WAL/SHM 与恢复副本混用。恢复操作会回退备份后的状态；自动化恢复测试使用固定 fixture。本次正式升级未执行回退或替换原库，保留了升级前备份。

## P3 schema 22 升级与恢复

当前0.3.0代码使用 schema `22`，2026-11-09用户已在Windows部署，运行库schema22与基本运行状态经 [只读检查](.scratch/windows-deployment/runtime-status-2026-11-09.md)确认；本轮未执行迁移或重启。固定脱敏fixture与真实库的独立副本此前已验证迁移。2026-11-05任务12在新建隔离验收库完成受控真实消息测试，当时Linux既有库仍为schema16且未改变，见 [受控报告](.scratch/p3-news-and-global/controlled-acceptance.md)。其他数据库的升级/启动仍需确认精确目标、备份、副本预演及消息范围；不要用正常启动代替离线预演。

使用上节同一 `ff14_app.migration_preview` 命令，只读复制源库到全新目标后迁移。16→22保留快照、策略、查询授权、已发送窗口、unknown及attempts；区域转换为原单元素集合，公告默认关闭。迁移不填造逐活动核实时间，也不从当前标题伪造历史比较字段；旧活动首次有效复查后才取得主动展示资格。

版本化原始备份为 `.pre-v16-to-v22.bak`（源版本不同则16相应变化），首次生成后不覆盖；`.pre-migration.bak`为最近一次迁移前副本。中断后可重试，拒绝更高版本自动降级。恢复时沿用上节的停应用、保留失败库/WAL/SHM、在新目录恢复备份及完整性/外键检查流程。回退代码须配套对应旧schema备份；不可将22库交给16代码。本轮备份恢复演练仅在临时目录完成。

## P3 运行参数与诊断

本机 `/settings` 持久保存正整数参数：列表默认15分钟、摘要预查提前5分钟、年龄警告48小时、停发72小时；警告必须小于停发阈值。产品含义见 [GOAL](GOAL.md#1-官方资讯订阅)。新闻列表、已知正文队列与投递分别唤醒，不补跑停机期间的轮询刻度。

正文队列每分钟评估，成功正文通常24小时复查；每轮按来源交错选取、总量最多40篇、并发最多4篇；某来源持续失败不会占满其他来源的预算，各来源内部仍按最久未尝试等既有顺序选择。国服单次HTTP最多3次20秒尝试，NA详情最多2次12秒尝试；5分钟预查是可调工作预算，不能保证所有请求完成。到点根据已有核实结果发送，慢请求不延后整个群；关闭时取消任务并丢弃迟到正文结果。NA Atom本身含完整正文时按逐篇内容核实，列表304不推进活动年龄。

P3结构化诊断同时输出控制台和 `data/logs/p3.log`，标准库轮转为单文件2MiB、3个历史文件，总计约8MiB；单条超长记录受安全摘要限制。首次目录/文件创建失败明确阻止静默启动。日志使用UTC，关联来源/文章、内部目标UUID、阶段、attempt及处理结果；连接URI、敏感字段和平台目标号不写入P3诊断。重复同类失败计数/节流，完整业务状态仍在SQLite，不依赖日志恢复。

`/sources` 查看来源和逐活动失败，`/articles` 查版本/证据，`/deliveries` 查unknown与attempt。普通命令回复结果的完整处理及通用审计导出仍留P5；P3日志不等于完整运维Dashboard。

## 统一运行入口

将 `.env.example` 复制为被忽略的本机 `.env`，填写 NapCat 正向 WebSocket 地址和 token 后运行。Bot 所有者与服务群白名单在 WebUI 的“群授权”页面管理：

已有 `.env` 时保留它，在目标机器检查连接地址是否仍可访问；不要在日志或提交中复制其值。

Linux：

```bash
bash scripts/start-nonebot-dev.sh
```

Windows：

```powershell
.\scripts\start-nonebot-dev.ps1
```

统一 Python 入口固定监听 `127.0.0.1`，Linux 脚本也固定 `HOST=127.0.0.1`，默认端口为 `20036`。两个脚本启动同一个 Python 模块。入口持有 `data/runtime.lock` 的系统文件锁；第二个实例会拒绝启动。关闭进程会释放锁，锁文件保留是正常现象，不要删除锁文件来绕过运行保护。

使用 `ONEBOT_V11_WS_URLS=[]` 可以禁止主动连接 NapCat，但这不是离线测试模式：正常入口仍会初始化本地数据库、启动来源网络轮询和投递任务。离线生命周期核验必须使用临时工作区、临时数据库、无连接配置及网络拦截，不能直接在真实 `data/` 上执行。连接真实 NapCat 前必须确认同一测试群没有另一套网关处理事件。

WebUI 已挂载在同一进程：`http://127.0.0.1:20036/`，群授权页为 `/access`。不要再单独启动 WebUI；旧的 `start-webui.ps1` 只是调用统一入口的兼容包装器。
