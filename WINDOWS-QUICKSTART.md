# Windows 解压部署

适用：FFXIVBot 0.3.0，64 位 Windows、Windows PowerShell 5.1 或更高版本。此 ZIP 是需要联网初始化的源码部署包，不含 Python 和第三方依赖；不含任何真实连接凭据、群配置、数据库或日志。完整流程见 [Windows 部署指南](WINDOWS-DEPLOYMENT.md)。

## 第一次使用

1. 将 ZIP **完整解压**到当前用户自己的目录，例如 `%USERPROFILE%\Apps\FFXIVBot`。不要在压缩包浏览窗口内直接运行，也不要覆盖正在运行的旧目录。
2. 普通双击 `Start-FFXIVBot.cmd`，不需要“以管理员身份运行”。脚本会自动下载固定版本 uv、安装当前用户范围的 Python 3.14、按 `uv.lock` 安装依赖，再检查依赖、时区和全套离线测试。任何一步失败都会停止，窗口保留错误。
3. 首次运行自动创建 `.env` 并打开记事本。填写**既有** NapCat 的正向 WS 地址和 token，保存并关闭。地址必须为 JSON 数组；参考同目录 `.env.example`。脚本不会安装或修改 NapCat，也不会修改 Koishi。
4. 默认新建配置为 `ONEBOT_V11_WS_URLS=[]`，表示不连接 QQ。保留它可先检查本机管理页面，但启动仍会访问官方来源并写数据库。
5. 启动前会提示当前连接状态、后台任务及已有库恢复投递的影响。确认旧实例已经停止、真实目标范围已获准后，输入 `START`；其他输入取消。
6. 浏览器打开 <http://127.0.0.1:20036/>。在“群授权”设置所有者和精确群号，再分别配置公告、活动摘要及维护。保持终端窗口打开，`Ctrl+C` 停止。

以后继续双击同一个 `Start-FFXIVBot.cmd`；依赖缓存会复用，脚本仍做锁定同步和离线检查。不要复制此目录再同时启动另一份 Bot：进程锁只能保护同一目录。

## 当前用户与 UAC

- 默认以双击脚本的 Windows 用户运行，uv 位于 `%LOCALAPPDATA%\FFXIVBot\tools\uv-0.12.4`，托管 Python 位于 `%LOCALAPPDATA%\FFXIVBot\python`，应用依赖位于解压目录 `.venv`。
- 不改系统 PATH、注册表、系统执行策略、防火墙、服务或开机任务。脚本的 `ExecutionPolicy Bypass` 只用于此次 PowerShell 进程，不覆盖企业组策略。
- 目录不可写时，脚本会先解释范围。可选择移动到自己的目录，或输入 `UAC` 后由系统弹窗提权，**仅将该解压目录的可继承“修改”权限授予最初运行用户**。管理员辅助进程结束后，安装与 Bot 仍在原用户进程中运行；取消 UAC 或修复失败会停止，不循环请求。
- UAC 不用于修复网络下载、企业执行限制或测试失败。只有自己的用户工具目录也不可写时，应检查账户配置，不把整个安装程序切换成管理员。
- 目录权限修复不会收紧原有其他账户权限，`.env`、`data` 和备份应保存在当前用户私有目录中。

安装行为基于 [uv 固定版本安装方式](https://docs.astral.sh/uv/getting-started/installation/)与[自定义安装目录说明](https://docs.astral.sh/uv/reference/installer/)；按需提权采用 [Microsoft Start-Process RunAs](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process)和[icacls](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)（2026-11-08 核验）。

## 仅准备环境，不启动 Bot

```powershell
.\Start-FFXIVBot.cmd -CheckOnly
```

该模式可能联网安装依赖；安装后的测试离线运行，不创建 `.env`、不迁移业务数据库、不启动来源采集或 QQ 连接。若已有 Bot 在运行，会在同步依赖前拒绝执行。

## 带入已有数据

先按 [迁移步骤](WINDOWS-DEPLOYMENT.md#从-linux-或旧-windows-实例迁移)停止旧实例、冷备并复制完整 `data/` 和 `.env`，不要复制旧 `.venv`。如果已有 schema 需要升级，脚本先通过 SQLite Backup API 生成 `before.sqlite3`、复制连接配置和锁文件，再迁移 `preview.sqlite3`，报告保存在 `data/backups/windows-deploy-*/`。原库只会在输入 `START` 后由应用正常启动迁移；预演失败就停止。

同版本数据库检查完整性后启动，不在每次运行时重复制作全量备份。恢复历史备份可能丢失近期投递账本，不要用删库或复制旧库来修复连接问题。停机升级前的独立冷备、保留原代码及日常备份仍由维护者负责，详见完整指南。

## 后台运行、更新和排错

- 启动器是前台运行工具，关闭窗口或注销可能终止 Bot。需要开机运行时，完成初始化后按 [任务计划程序配置](WINDOWS-DEPLOYMENT.md#6-用任务计划程序实现开机自启)，使用**当前用户**、同一个 `.venv\Scripts\python.exe` 和相同工作目录；不要把交互安装器直接放进计划任务。
- 移动文件夹后重新运行启动器。损坏或 Python 系列不匹配的 `.venv` 会重命名为 `.venv.previous-*` 留存；不会删除你的旧环境。更新前停 Bot、禁用自动启动并冷备，不允许运行中安装依赖。
- 安装失败先检查网络能否访问 `astral.sh`、GitHub 和 Python 包下载站；脚本不修改系统代理或自动降低 TLS 校验。已有缓存是否足以离线安装取决于本机缓存，本 ZIP 不是离线安装包。
- 测试失败、争锁失败或预演失败时保留输出，禁止跳过失败直接上线。不要发送包含 `.env`、token、地址或真实群号的截图。
- 程序诊断见 `data/logs/p3.log`；安装早期错误保留在当前控制台。WebUI 没有认证，保持回环监听，不向公网开放。
- `PACKAGE-MANIFEST.json` 记录包内文件 SHA256，ZIP 旁的 `.sha256` 可校验下载/复制完整性；摘要不代替数字签名。开发过程的 `.scratch` 证据未打入包，部分历史文档链接需在源码仓库查看。

本包经过 Linux 离线检查与打包内容核验；Windows 安装/UAC、原生锁互操作、真实连接和长期运行仍需目标服务器验收，不将脚本生成视为实机验收通过。
