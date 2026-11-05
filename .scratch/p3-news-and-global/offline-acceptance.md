# P3 离线验收记录

> 2026-11-05修复后重新验收：任务13的R1–R4已修复，136项完整离线测试通过；当前结论与证据见 [任务13验收报告](task13-acceptance.md)。下文保留2026-11-04的127项历史记录及其限制。后续任务12已获授权并完成受控范围，最新137项及真实证据见 [受控报告](controlled-acceptance.md)。

日期：2026-11-04（Asia/Shanghai）。范围：任务01–11；任务12未开始。保留接手时工作区全部已有修改，未提交或回滚用户工作。产品规则仍归GOAL，本文只记实际执行证据。

## 最终结果

`UV_CACHE_DIR=/tmp/ffxivbot-uv-cache bash scripts/check-linux.sh`：127项离线测试通过，锁文件检查及30个已安装包兼容性检查通过。完整输出保存于 [offline-check.log](offline-check.log)。本轮结果不引用历史94项记录替代。

执行环境：Linux `.venv-linux`，Python 3.14.9、uv 0.12.4。依赖声明与锁文件未变；未安装新依赖。临时UV缓存用于避开沙箱内全局缓存只读问题。

其余实际通过命令：

```bash
bash -n scripts/start-nonebot-dev.sh scripts/check-linux.sh
git diff --check
.venv-linux/bin/python -m compileall -q ff14_core ff14_app ff14_web plugins tests
```

文档相对路径与锚点检查见本记录末尾。初次沙箱内线程测试曾卡在asyncio唤醒；该临时测试进程已终止，最终检查在允许线程唤醒的执行环境运行，仍只使用fixture、fake端口和临时库。

## 切片证据

| 任务 | 实现入口 | 自动化证据 |
| --- | --- | --- |
| 01 | 国服JSON、NA新闻/Topics Atom及直接正文契约 | `test_p3_contracts`、`test_sources`；[公开样本说明](../../tests/fixtures/p3/README.md)、[本次事实](../../DATA-SOURCES.md#p3-契约复核2026-11-04-实测) |
| 02 | RuntimeSettings、ApplicationWorkers、/settings | `test_p3_workers`：默认/修改周期、重启、重叠唤醒、持久结果与非法输入 |
| 03 | article_version/probe、机械比较、/articles | `test_p3_documents`：格式、标题、数字、图片/链接、重复抓取、A→B→A、规范化基线、HTML转义 |
| 04 | AnnouncementWorkflow、独立scope/基线、既有claim/attempt | `test_p3_news`：启用边界、旧文/未知时间、关闭重开、后续变动、未知结果、待处理观察与维护分流 |
| 05 | news_batch/member、逐页成功基线 | 同上：汇总空结果/每日窗口、晚到文章、72小时内/外/恰好边界、部分成功后中断与重启 |
| 06 | 三流独立区域集合、区域分页和原子领取 | `test_p3_regions`：本机DTO、空/非法集合、移除区域、同ID不同provider维护隔离；原权限矩阵回归 |
| 07 | 双Atom正文、LodestoneDetailService、na-time-v1 | `test_p3_na_freshness`：真实维护/跨月样本，合成跨年/多区间/PST-PDT/缺时区/日期精度/不确定时间 |
| 08 | event_verification、EventFreshness、/sources | 同上：不变正文刷新年龄、零条保留、48/72小时边界、对应详情304、机器时区无关、首次观察时间保留 |
| 09 | ReviewCoordinator、preflight、digest_incident/member | `test_p3_preflight`：合并多群、阻塞HTTP到点发送、迟到不重发、停止丢弃结果、预留后重启/改时刻恢复；年龄门禁、暂停/恢复与正常空结果 |
| 10 | 安全诊断、轮转、现有本机页面与账本 | `test_p3_diagnostics`：固定伪凭据脱敏、异常栈、轮转容量；服务捕获失败也写持久核实状态 |
| 11 | 唯一应用装配、schema17–22增量、备份/副本预演 | `test_p3_migration`、`test_runtime_lifecycle`、既有delivery/runtime-lock/权限回归及完整suite |

上述测试模块均由最终 `unittest discover` 实际执行；相关切片实施过程中另执行过相应模块组合。测试断言fake端口的可见文本、候选/attempt状态、保留快照与本机HTML，不创建真实投递成功记录。

## 迁移与恢复

固定 [schema16脱敏旧库](../../tests/fixtures/p3_schema_v16.sql)含来源快照、旧详情版本、领域候选、显式策略窗口、查询授权、所有者、归档目标、sent/uncertain与attempts。

- 独立副本16→22预演；源文件SHA-256保持不变。
- 17→18→19→20→21→22按实际结构连续升级，各阶段重复初始化并检查完整性与外键；也覆盖各版本标记下缺失结构的中断重试。
- 首次版本化备份在失败重试后校验和不变；恢复到全新文件后再迁移通过；较高schema拒绝自动降级仍通过既有回归。
- 保留原单区域、原显式窗口、普通成员查询授权及历史投递键；公告默认关闭，不自动加入NA。
- 旧候选的核实时间与缺失精度保持未知；不以迁移时间填成功，也不从当前标题反填旧比较版本。

本轮迁移只涉及临时fixture，不读取、升级或回放正式数据库。正式升级流程见 [运行说明](../../UV-USAGE.md#p3-schema-22-升级与恢复)。

## 运行与可见行为

本机/settings、/news、/articles、/sources与既有/access、/deliveries已贯通；[WebUI说明](../../WEBUI.md)记录入口。唯一NoneBot生命周期离线测试验证三个应用唤醒任务、八个页面、127.0.0.1监听、停止/锁与完整性；所有外连被拦截。

普通公告保留原文标题、中文分类、区域与北京时间。维护沿用独立流。手动预览不写delivery；自动摘要正常空结果静默，过旧逐项排除，全量不可用持久去重暂停，核实恢复后按当前状态恢复。已有发送尝试和unknown不因重启、换模式或换日程生成重复投递。

## 限制与任务12前置条件

- 上游只读在线核验只属于01；本次未连接真实NapCat、启动正式应用、发送真实群消息或升级正式库。
- NA仅覆盖明确标签与证据支持的时间形态；日期范围保留day证据，缺时区/缺结束/不确定时间不生成精确提醒。没有通用自然语言时间覆盖承诺。
- 国服有限5页和NA有限feed不能穷尽旧文修改；已知正文周期/预查补足一部分观测。相同图片地址下的字节变化不检测。上游304支持本次未获在线证据，仅离线验证正确退化和对应版本条件。
- 预查5分钟为可调预算；最多4并发、后台每轮40篇。超时/尚未完成的结果到点按新鲜度退化；不承诺全部文章在预算内完成。
- 无跨文章自动合并、国服自动取消、其他国际区、翻译/摘要或P4市场功能。普通回复结果的完整处理、全套运维审计与长期观察仍留P5。
- 任务12需另确认精确目标、消息流/数量预算、时间窗口、当前正式库版本与备份/副本预演、唯一实例检查、停止/恢复方式。历史真实测试授权不继承。

文档校验：27份关联文档、170个本地链接/锚点全部有效；过时P3未实现描述已检索并修正。新增fixture仅含公开文章样本和显式合成账号/伪凭据，未读取本机连接文件。`git diff --check`、启动脚本语法及Python编译通过。
