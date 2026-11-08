# FFXIVBot 实施清单

状态基准：2026-11-05 P3任务01–11及复审修复13已完成；任务12已按用户授权完成受控真实验收，8条消息经provider读回及群客户端确认；实测发现的Topics标题缺陷已修复，完整137项离线测试通过。正式库仍为schema16，正式部署与长期运行未验收。2026-11-05追加国服实时采集到真实投递全链路验收通过，当前代码版本为 `0.3.0`，正式环境尚未部署。这里仅记录已验证事实和下一步工作；产品目标见 [`GOAL.md`](GOAL.md)，开发与验收规则见 [`AGENTS.md`](AGENTS.md)，当前发行记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 当前发行基线

- 2026-11-09 GitHub 托管迁移准备：目标为 [`FreezeNovo/FFXIVBot`](https://github.com/FreezeNovo/FFXIVBot)，已补充 [仓库首页](README.md)，当前文件与历史敏感信息检查未发现匹配；锁文件、依赖兼容检查及148项离线测试通过（147项成功、1项Windows专属跳过）。按用户决定保留原始提交历史与`v0.3.0`标签，等待本机GitHub登录后推送；暂不配置云端开发平台，不迁移本地业务状态或启动正式应用。
- 2026-11-09 Windows实机部署阻塞已定位，见 [只读诊断](.scratch/windows-deployment/diagnosis-2026-11-09.md)：实际Python3.14.7可执行，但uv的`cpython-3.14`目录联接被系统以不受信任装入点拒绝（os448），引用该联接的`.venv`报trampoline os2。需修复启动器选择真实解释器路径并保留重建环境；尚未实施。目标无`.env`、业务数据库或应用日志；未修改远端安装、权限或启动应用，Windows部署仍未验收。
- 2026-11-06 用户确认 Linux 开发、Windows 服务器部署；已按当前代码核对并补充 [Windows 部署指南](WINDOWS-DEPLOYMENT.md)。本次仅文档变更，未重建旧 Windows 环境、未启动应用或迁移正式库；Windows 原生依赖安装、全套测试、系统锁、开机自启、恢复及长期运行仍待目标服务器实测。
- 2026-11-08 增加 [解压部署包及快速开始](WINDOWS-QUICKSTART.md)：`Start-FFXIVBot.cmd` / `scripts/start-windows.ps1` 自动安装当前用户范围的固定 uv、Python 3.14及锁定依赖；串行安装和运行锁保护，失败即停，只读验证配置，旧库先备份/副本预演，输入 `START` 后才加载网关。UAC仅按需修复原用户对解压目录的权限，不提权安装器或Bot。`scripts/build-windows-package.py` 用允许清单生成不含本地状态的ZIP与SHA256清单。新增10项Linux可执行自动测试，另有1项Windows专属.NET/msvcrt锁互操作测试；生命周期测试保留Windows必要环境并在网络拦截前创建事件循环。Windows安装/UAC和真实部署仍未实测；本轮未启动正式应用、未迁移正式库或发送消息。
- 本轮验证：`UV_CACHE_DIR=/tmp/ffxivbot-uv-cache bash scripts/check-linux.sh` 锁文件与30包兼容检查通过，148项测试中147项通过、1项Windows专属测试跳过（Linux Python 3.14.9）。受限执行环境的asyncio跨线程唤醒曾停滞，检查在获准的隔离限制外离线运行后通过；未以此修改业务实现。Microsoft官方便携PowerShell 7.4.6在Linux上完成启动脚本解析、native退出码、空格/中文/单引号路径和写入探针清理检查；这些不替代Windows PowerShell 5.1与UAC实测。
- 当前代码版本：`0.3.0`（2026-11-05），包含P3、任务13修复及此前可靠性修改；`0.2.0`为历史发行基线。
- 代码schema为`22`；正式本机数据库仍为`16`，未升级或留下常规运行实例，升级行为见 [`UV-USAGE.md`](UV-USAGE.md#p3-schema-22-升级与恢复)。
- 国服实时列表20篇→详情正文→机械版本→群策略→公告候选→持久attempt→真实投递/读回→人工确认已通过，1条消息且重复tick/服务重建均不重发，见 [全链路报告](.scratch/p3-news-and-global/cn-end-to-end-acceptance.md)。采用隔离库受控回放，正常新订阅不补历史也已验证。

## 工程技能配置

- [x] 2026-10-30 配置本地 Markdown [任务跟踪](docs/agents/issue-tracker.md)、默认[分诊标签](docs/agents/triage-labels.md)和 single-context [领域文档规则](docs/agents/domain.md)，入口见 `AGENTS.md` 的 Agent skills。文档链接、标签映射、空白检查及 `uv lock --check --offline --no-python-downloads` 通过；本次仅修改文档。
- 历史验证限制（2026-10-30）：现有 Windows `.venv` 引用的 Python 3.14 不在原路径，`.venv/Scripts/uv.exe pip check` 和 `.venv/Scripts/python.exe -m unittest discover -s tests -v` 均未能启动检查；本日后续已在独立 Linux 环境补验，结果见下方恢复开发核查，原 Windows 环境保持不变。

## 恢复开发核查（2026-10-30）

- [x] 在独立 `.venv-linux` 安装原锁文件依赖，保留 Windows `.venv` 和本地状态；Python 3.14.8、项目 uv 0.12.4，锁检查、30 个已安装包的兼容检查及原有 61 项离线测试全部通过，补齐上方 Linux 验证缺口。命令见 [`UV-USAGE.md`](UV-USAGE.md#linux-前置条件与环境恢复)。
- [x] 补充 Linux 统一启动脚本；临时工作区内完成 NoneBot 插件加载、WebUI 挂载、后台任务启动/关闭和 SQLite schema 15 完整性烟测。测试禁止外部连接，未读取真实连接配置或迁移真实数据库。
- 核查当时能力：国服发现/详情/活动摘要已形成闭环，国服维护流已实现；当时国际服只有Atom发现、通用公告未实现。后续P3增量见本页P3记录，市场仍延期。P0–P2 的已勾选项表示已有实现与对应验证，不能解释为所有可靠性不变量均已满足。
- [x] 已于 2026-11-02 修复[投递不确定结果与关闭策略后的待发送消息](.scratch/linux-resumption/issues/01-delivery-recovery.md)。原缺陷：上游接收后响应超时会再次发送，关闭策略仍发送，发送完成后才记 attempt。修复证据见下方。
- [x] 已于 2026-11-02 修复[群命令权限与功能策略检查](.scratch/linux-resumption/issues/02-command-permissions.md)：原来的普通成员诊断越权已阻止，活动/维护查询增加独立授权及真实 NoneBot 事件测试。
- [x] 已于 2026-11-02 修复[移除群导致投递历史级联删除](.scratch/linux-resumption/issues/03-delivery-history.md)：改为归档并保留账本，原先接受错误行为的测试已替换。
- 当前架构仍有过渡实现：应用服务直接依赖 `StateStore`；后台任务当时在网关插件中，现已由P3移至应用workers。群摘要受到全局日历分类影响的问题已在本次修复。后续按功能修改逐步落实目标边界，当前不作全仓重写。
- 原核查缺口中的真实 NoneBot 事件权限、发送确认前中断与重启恢复已补离线测试。HTTP transport 的退避/超时、部分分页失败仍需补充更完整的专项覆盖；真实 provider 的进程强杀和网络异常仍待受控验收。
- 下一步顺序（2026-11-05 更新）：P3任务01–13在各自范围内完成，见 [受控验收报告](.scratch/p3-news-and-global/controlled-acceptance.md)。测试连接已关闭，正式升级/部署与长期观察需后续单独授权；市场目录与阈值仍留P4前确认，本轮不启动P4。
- 2026-10-30该轮未重新核验真实上游接口、NapCat 连通性、真实群投递和长期运行；后续受控验收见P3任务12。正常入口即使设置 `ONEBOT_V11_WS_URLS=[]` 仍会启动源轮询，不能用作无副作用测试入口。

## 可靠性与权限修复（2026-11-02）

- [x] 发送前事务领取并持久记录 attempt；明确离线未发送可重试，超时/缺失消息 ID/中断进入未知结果且不重发。WebUI 增加记录查看与核查关闭，保留审计。证据：`tests/test_reliability_regressions.py`、`tests/test_gateway.py`、`tests/test_web_access.py`。
- [x] 发送前重查群和消息流/子类型策略、区域、提醒提前量及有效期；关闭策略期间的在途请求完成后不再重试。移除群归档、保留历史，重新添加不恢复旧授权。证据：`tests/test_reliability_regressions.py`、`tests/test_calendar.py`。
- [x] 诊断权限只认数据库所有者，覆盖别名、群主角色伪授权、未启用群和私聊；群员活动/维护查询独立授权。网关把来源 CQ 文本作为普通文本发送。证据：`tests/test_application.py`、`tests/test_gateway.py`、`tests/test_web_access.py`。
- [x] 同数据目录进程锁防止第二实例误执行启动恢复；统一入口固定回环监听。证据：`tests/test_runtime_lock.py` 的真实子进程争锁与释放检查；Linux 已验证，Windows 分支未执行。
- [x] 修复同一天修改发送时刻重复摘要、旧日期加时间键升级后重复、分页缩减残留/增长无法恢复、长单行未拆分、当前时刻恰好开始的活动遗漏、月历分类误影响群查询。证据：`tests/test_reliability_regressions.py`、`tests/test_calendar.py`。
- [x] 修复过早公告永久抑制提前提醒、观察写入后中断丢通知、无时间变化的 parser 升级产生重复维护通知、已过期维护仍发旧提醒。证据：`tests/test_maintenance.py`。
- [x] schema 16 保留历史、停止旧队列直接补发、迁移重试不覆盖首次版本化备份、拒绝高版本降级；新增只迁移独立副本的预演命令。证据：`tests/test_delivery_migration.py`、`tests/test_migration_preview.py`，固定 schema 15 fixture 完整性、外键、恢复后再次迁移通过。实际原库未迁移。
- [x] 94 项离线测试通过；锁文件和依赖检查、Python 编译及差异空白检查通过，命令见下方。`tests/test_runtime_lifecycle.py` 验证 schema 16 的启动/关闭、四个 WebUI 页面和回环监听；临时数据库的 `/access` 与 `/deliveries` 另经浏览器检查；未连接真实 NapCat 或发送群消息。

本次完整验证命令（Linux，仓库根目录）：

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux .venv-linux/bin/uv lock --check --offline --no-python-downloads
.venv-linux/bin/uv pip check --python .venv-linux/bin/python
.venv-linux/bin/python -m unittest discover -s tests -v
.venv-linux/bin/python -m compileall -q ff14_app ff14_core ff14_web plugins tests
bash -n scripts/start-nonebot-dev.sh
git diff --check
```

剩余限制：未知结果当前仅支持核查关闭，不支持重发；远端无业务幂等承诺时不能保证网络异常下恰好一次。真实 NapCat/群权限、维护消息、长期连接、Windows 启动和系统锁仍需复验。本次未改动上游契约，不以离线 fixture 代表真实接口仍然有效。

## Linux 开发切换与正式数据库升级（2026-11-02）

- [x] 用户确认应用未运行并授权升级，核查未发现运行进程或数据库占用；持有同目录运行锁执行迁移，全程禁止外部连接。
- [x] 正式数据库由 schema 15 升级到 16：先创建独立备份并验证副本迁移，再升级原库；升级前后逐表比较所有原业务字段，来源快照、所有者、群策略、9 条已发送通知和 17 条投递尝试全部保留。完整性与外键检查通过；原库无待发送或未知结果队列，群员查询新授权默认关闭。
- [x] 备份、预演副本和不含账号/消息正文的汇总报告保存在被忽略的 `data/backups/linux-upgrade-20261101T183252Z/`；UTC 文件名对应本地 2026-11-02。`before.sqlite3` 为升级前备份，`preview.sqlite3` 为已验证副本，`report.json` 记录校验结果和备份校验和；自动版本化备份也保留在原库旁。
- [x] 后续开发和验收切换到 Linux `.venv-linux`，保留当前工作区路径与 Windows 历史环境；按原锁文件离线同步依赖。新增 `scripts/check-linux.sh` 作为校验入口，运行方式见 [`UV-USAGE.md`](UV-USAGE.md)。
- [x] `bash scripts/check-linux.sh` 通过：锁定 33 个包、检查 30 个安装包，94 项离线测试通过；两个 Linux 脚本语法检查和 `git diff --check` 通过。
- 未启动正式应用、读取或修改连接凭据，也未连接 NapCat、采集上游或向真实群发送消息。下一次启动会使用原有主动推送策略；群员查询需由所有者在 WebUI 显式启用。

## NapCat 真实链路验证（2026-11-03）

- [x] 用户明确授权 SSH 核查 Windows provider，并指定单一测试群发送最多一条消息。SSH 鉴权成功，NapCat/QQ 正在运行，未发现远端或本机正式 FFXIVBot 实例；未安装、重配、重启或停止 NapCat/Koishi。
- [x] 从 Linux 使用既有连接配置完成直接 WebSocket 鉴权，`get_version_info` 返回 NapCat.Onebot 4.18.19 / v11，`get_status` 返回 online/good，登录身份和指定目标群检查通过。版本仅为本次运行实例回报，不代表最新发行版。
- [x] 通过实际 NoneBot2 + OneBot V11 Adapter 和项目 `NoneBotMessagingPort` 完成连接、断开、离线结果分类与重连。测试期间不运行来源轮询、日常调度或入站命令回复。
- [x] 在正式库的独立副本中先持久登记 attempt，再向获准目标发送一条明确标注的 Linux 连通性测试消息；provider 返回有效消息 ID。随后只读 `get_msg` 验证正文、消息 ID 和目标群完全一致。独立账本只有一次发送尝试，已发送候选不能再次领取；没有重发或回放原队列。
- [x] 使用实际配置快照核查所有者诊断、普通成员拒绝、群主角色不越权、查询未授权、未知群与私聊静默；这些是本地 CommandService 检查，不能等同于真实 QQ 入站事件验收。
- [x] 测试连接已关闭，正式库文件校验和不变，原 9 条通知与 17 次尝试不变；远端 NapCat/QQ 原进程仍在。诊断脚本、副本账本和脱敏报告保存在被忽略的 `data/live-tests/linux-napcat-20261103-02/`，不是生产投递历史，不应作为常规启动或自动重跑入口。
- 诊断工具的首次尝试因未初始化框架生命周期而在发送前停止，发送次数为零，记录保留在相邻无 `-02` 后缀目录。第二次已发送成功后，诊断工具的读回解析遇到原始消息段字典与 `Message` 构造器不匹配；改用独立只读核验完成确认，未再次发送。这两项是临时诊断工具问题，未修改产品发送逻辑。
- [x] `bash scripts/check-linux.sh` 再次通过：锁文件、30 个已安装包兼容检查及 94 项离线测试通过；文档链接与 `git diff --check` 通过。
- 尚未覆盖：真实 QQ 入站的所有者/普通群员/私聊矩阵、日常活动摘要与维护调度、来源实时抓取、长时间断网或进程强杀恢复。测试结束后正式应用仍未启动；provider 读回通过不等于群成员客户端的人工收件确认。

## 已完成的 PoC

- [x] 建立 Python 3.14 + UV 工作区，Core 不依赖 QQ 框架。
- [x] NoneBot2 + OneBot V11 Adapter 连接既有 NapCat OneBot 11 WebSocket Server。
- [x] 在真实测试群验证受限的 `ff14 状态` 回复，其他群和私聊保持静默。
- [x] 验证 FF14 Bot 与现有 Koishi 独立连接，互不安装、配置或停止对方。
- [x] 实现国服新闻最近 5 页发现，当前最多缓存约 100 篇文章。
- [x] 实现 Lodestone Atom 文章发现和 source health。
- [x] 实现国服详情手动抓取、原始快照、明确时间区间解析和 parser 版本。
- [x] 实现国服季节、联动、运营、直播、维护和其他运营活动分类。
- [x] 实现本机 FastAPI WebUI 月视图及全局分类开关。
- [x] 在测试群验证数据源刷新、状态、国服详情解析和候选展示。
- [x] 2026-08-24 人工验证分类候选、WebUI 官方详情链接和活动摘要真实消息投递。
- [x] `ff14 数据源` 同步文章后自动解析最近 40 篇国服详情；独立详情命令仍可用于补充复查。
- [x] 恢复开发基线的 61 个离线测试通过；当前 suite 与本次新增回归结果见上方修复记录。

## 当前限制

- QQ 网关唯一运行时是 NoneBot2；业务命令已抽到 `ff14_core.application.CommandService`。分类、详情链接和定时投递已完成测试群人工验收；断线、长期运行等边界仍需持续观察。
- P0 已完成独立应用入口、NoneBot 插件加载烟测和旧网关清理；2026-08-24 已记录测试群连接与活动摘要人工验收，2026-11-03 已完成 Linux 真实连接、重连与单条发送/读回复验（见上方记录），维护代表场景已于P3任务12以明确标识的历史样例完成群客户端人工验收，当前维护的长期调度仍未验收。
- 来源自动轮询已由统一 NoneBot 进程后台任务提供；真实 NapCat 连接下的长期运行仍待验收。
- 来源传输已支持超时、代理入口、条件请求、有限退避和 schema 退化记录；真实上游的 ETag/304 行为仍需在线观察。
- 国际服当前仅实现NA新闻/Topics正文与明确时间；JP/EU/OC、翻译/中文摘要及任意自然语言时间解析均未实现。上游观测盲区见 [数据源契约](DATA-SOURCES.md#p3-契约复核2026-11-04-实测)。
- WebUI 已支持每群活动摘要的区域、稳定分类代码、时间和时间窗口；月历的历史分类筛选仍保留全局兼容策略。
- 解析成功为零条时会记录空结果并保留最后有效候选；parser/schema 退化会保留旧快照并记录错误。国服当前不做自动取消判定，活动时间调整会替换未来候选。
- 详情复查队列不再只依赖最新 N 篇；队列容量和复查间隔仍需结合生产数据调优。
- 新公告已扩展既有账本，具有独立scope、群基线、文章版本、成员与批次；普通命令回复结果的完整处理仍属P5。
- 活动摘要已具备查询、规划、持久候选、幂等、时区/跨日、来源提示和逐目标重试闭环，并已完成人工测试群定时发送验收。
- 国服维护流已具备独立策略、立即新公告、24/6/1 小时提前提醒、时间变更通知、`ff14 维护` 预览和独立投递；公告、提前提醒和时间变更可分别关闭，取消仍不由解析为空推断。
- 当前维护时间变更以同一官方文章 ID 的正文新版本为稳定关联范围；独立的“调整公告”尚未跨文章合并为同一维护，保守地作为新维护公告处理。
- WebUI 无认证，只能监听 `127.0.0.1`。

## P0：应用与 QQ 网关边界

- [x] 建立独立应用入口，使用 NoneBot2、OneBot V11 Adapter 和 `~fastapi+~websockets` 驱动。
- [x] 实现私有本地 `qq_nonebot` gateway 插件，只处理事件映射、回复和主动发送。
- [x] 将插件内命令 `if/elif` 移到 Core `CommandService`，保留 `ff14` 命名空间。
- [x] 定义 `CommandRequest`、`OutboundMessage`、`MessagingPort` 和通道无关错误。
- [x] 将群身份改为内部 target UUID + provider + provider target ID，移除 Core 对普通 QQ 群号的直接依赖。
- [x] 为连接状态、主动发送、超时和平台错误增加框架无关测试替身。
- [x] 将 NapCat URI/token 移入独立 `.env` 配置，并提供不含真实凭据的 `.env.example`。
- [x] 将 Bot 所有者和服务群白名单迁入 SQLite，并在本机 WebUI 提供管理、启用和移除操作。
- [x] 删除旧 QQ 网关源码、配置、状态、日志和运行依赖；不保留回退路径。

## P1：来源与活动可靠性

- [x] 为活动候选增加结构化 region 和稳定 classification code；显示标签不再作为策略主键。
- [x] 区分“成功解析为零条”和“parser/schema 退化”，后者保留最后有效候选并报警。
- [x] 为仍在进行或未来结束的活动建立详情复查队列，不依赖最新 N 篇窗口。
- [x] 将原始详情保存为不可变版本；内容 hash 未变化时不重复解析。
- [x] 为国服列表/详情和 Lodestone Atom 增加固定 fixture、schema 变化检测和条件请求。
- [x] 统一 HTTP transport：明确超时、代理、标识性 User-Agent、错误分类和带抖动退避。
- [x] 实现自动 source poller；单源失败不阻塞其他来源，也不清空旧快照。
- [x] 确定并实现可重复 SQLite schema migration，启用 WAL、备份和恢复验证。
- [x] 在测试群复验分类候选和 WebUI 官方详情链接（2026-08-24 人工验证）。

## P2：国服活动摘要闭环

- [x] 实现 `CalendarQueryService`，正确查询进行中、即将开始和即将结束活动。
- [x] 实现紧凑纯文本 renderer、长度拆分和 `ff14 活动` 预览命令。
- [x] 建立 target、activity digest policy、schedule、notification candidate 和 delivery 表。
- [x] 在 WebUI 为每个群配置活动摘要开关、区域、分类、时间和时间窗口。
- [x] 实现 planner、dispatcher、幂等键、逐目标重试和离线 Bot 延后投递。
- [x] 使用同一 query/renderer 完成每天 20:00 向一个测试群发送国服正在进行活动（2026-08-24 人工验证真实消息）。
- [x] 覆盖重启、重复唤醒、显式新鲜度策略、跨日、时区和未投递活动更新时间调整测试；国服取消判定不属于当前范围。

## P3：新公告、维护与国际服

[规格与任务图](.scratch/p3-news-and-global/spec.md)中的01–11已有实现，历史证据见 [验收报告](.scratch/p3-news-and-global/offline-acceptance.md)。2026-11-05 [代码复审](.scratch/p3-news-and-global/review-2026-11-05.md) 复现2项P1及2项P2缺陷；[修复任务13](.scratch/p3-news-and-global/issues/13-review-corrections.md)现已完成，任务11已重新验收，当前证据见 [修复验收报告](.scratch/p3-news-and-global/task13-acceptance.md)（136项通过）。随后用户授权的12受控验收已完成，最新 [受控报告](.scratch/p3-news-and-global/controlled-acceptance.md)记录8条真实消息、权限矩阵及137项离线测试；仅隔离副本升级，正式库未升级，未长期运行。下表保留各切片实现证据，不将测试通过等同于无缺陷。

| 任务 | 状态与代码/测试证据 |
| --- | --- |
| 01 来源契约 | 完成；DATA-SOURCES本次复核、`tests/fixtures/p3/`、`test_p3_contracts.py`。发现并修正NA Topics入口和国服置顶/页大小假设。 |
| 02 发现周期 | 完成；RuntimeSettings、ApplicationWorkers、本机/settings、`test_p3_workers.py`；持久周期、重启和重叠唤醒。 |
| 03 文章版本 | 完成；documents、P3StoreMethods、/articles和规范化只读预览、`test_p3_documents.py`；旧字段未知，不反填。 |
| 04 即时公告 | 完成，复审缺陷已由任务13修复；AnnouncementWorkflow、/news、群scope/基线、既有原子领取与attempt；`test_p3_news.py`。 |
| 05 汇总与补发 | 完成，复审缺陷已由任务13修复；持久batch/member、逐页成功基线、72小时边界与unknown隔离；`test_p3_news.py`。 |
| 06 独立多区域 | 完成；三流各自区域集合、旧策略保留、区域分页/领取复核与维护provider身份隔离；`test_p3_regions.py`。 |
| 07 NA正文与时间 | 完成，复审缺陷已由任务13修复；LodestoneDetailService、na-time-v1、两类Atom正文、day精度证据；`test_p3_na_freshness.py`。仅支持有明确证据的形态。 |
| 08 逐活动核实 | 完成；event_verification、结构化EventFreshness、48/72小时边界、失败保留与对应详情304；`test_p3_na_freshness.py`。 |
| 09 预查与退化 | 完成，复审缺陷已由任务13修复；ReviewCoordinator、预查持久预留、四并发/截止退化、一次故障周期、待发版本年龄复核；`test_p3_preflight.py`及上述新鲜度测试。 |
| 10 诊断 | 完成；安全结构化摘要、来源/文章/核实/账本关联、有界轮转；`test_p3_diagnostics.py`。完整P5平台未实现。 |
| 11 离线集成 | 完成，任务13修复后重新验收；固定schema16 fixture、副本预演、备份恢复、迁移重试、唯一本机生命周期及全套回归；`test_p3_migration.py`、`test_runtime_lifecycle.py`和验收报告。 |
| 12 真实验收 | 授权范围内完成；6条主动消息、2条命令回复均读回并经人工确认，所有者/非所有者角色切换/私聊/停用群入站通过；实时来源复核、重连与服务重建、unknown不重发、停用阻止发送。Topics栏目标题误拼已修复并新增回归，完整137项通过。正式库16未变、全部测试连接关闭；限制见 [受控报告](.scratch/p3-news-and-global/controlled-acceptance.md)。 |
| 14 国服全链路与版本同步 | 验收通过；实时官方列表/正文采集并投递1条，人工确认正常，重复唤醒和服务重建不重发；版本同步0.3.0，提交状态见 [任务记录](.scratch/p3-news-and-global/issues/14-cn-end-to-end-and-release.md)。 |
| 13 复审修复 | 完成；R1来源公平调度、R2自闭合VOID正文、R3过期首发后的更新资格、R4冻结页与后继变化隔离。`test_p3_review_corrections.py`新增9项回归，相关模块32项及完整136项通过；schema仍为22。 |

## P4：市场查询

- [ ] 选择并记录具备许可、可版本化更新的中文物品 catalog 来源。
- [ ] 实现本地物品名/别名检索和消歧：精确名 > 别名 > 前缀/包含 > 模糊匹配。
- [ ] 实现 Universalis v2 client、请求合并、批量、并发限制、TTL 缓存和 429/5xx 重试。
- [ ] 区分 NQ/HQ、没有挂单、可购数量不足、未观测和数据过旧，并显示上传年龄。
- [ ] 按不可拆分的整组挂单计算采购组合、实际买入量、超买量和总价，不能用最低单价乘需求数量。
- [ ] 实现本大区低价服务器推荐。
- [ ] 实现跨大区推荐：绝对收益、比例收益和最大数据年龄均达标时才展示。
- [ ] 在 WebUI 配置每群默认服务器/大区、品质和跨大区阈值。
- [ ] 增加 `ff14 价格` 命令及歧义、陈旧数据和上游失败测试。

## P5：运维与延后能力

- [ ] 延期：通用公告实质更新语义识别，按 [产品范围](GOAL.md#当前不做)在实际需要时再评估。
- [ ] 增加来源、调度、投递和 Bot 连接状态 Dashboard。
- [ ] 后续改善长公告差异的截断展示：当前QQ差异按200字符截取，长旧正文可能挤掉新值片段；WebUI完整差异和原文链接仍保留。2026-11-05任务13中发现，此展示问题不属于R1–R4，本轮未扩大修改。
- [ ] 在P3安全轮转日志和副本恢复验证的基础上补完整业务审计、配置导入/导出与运维演练。
- P3来源、逐篇解析、核实退化、暂停/恢复和主动投递失败已接入安全诊断、SQLite状态及本机页面，见本页P3任务10。普通命令回复结果当前仍未由调用方完整处理；完整监控/审计与长期日志运营仍留P5，不把P3日志等同于完整运维平台。
- [ ] WebUI 需要离开 localhost 时再实现认证、CSRF 防护和安全会话。
- [ ] 实际需要复杂交互时再评估 React/Vite；当前服务端渲染不是迁移阻塞项。
- [ ] 只有出现多实例或多个 writer 时才评估 PostgreSQL、leader lock 或外部队列。
- [ ] 官方 QQ Bot、社区 ICS、个人私聊订阅和价格到价提醒均不属于当前里程碑。
