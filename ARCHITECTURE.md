# FFXIVBot 架构设计

状态：目标架构与当前边界，更新于 2026-11-05。产品范围见 [`GOAL.md`](GOAL.md)，实现顺序见 [`TODO.md`](TODO.md)。当前发行记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 当前实现状态

当前 QQ 运行时已经统一为 NoneBot2 + OneBot V11 Adapter，通过正向 WebSocket 连接既有 NapCat。Core 应用、通道无关命令端口和本地启动烟测已经建立；分类候选、详情链接和活动摘要定时投递已在测试群人工验证，其他连接边界仍按 `TODO.md` 的验收项执行。

FFXIVBot 是独立、可部署的 Python 应用，不是准备发布到 NoneBot 商店的通用插件。应用内部使用一个私有本地 NoneBot 插件作为 QQ 网关；业务 Core、FastAPI、调度和存储不依赖 NoneBot。

```text
NapCat OneBot 11 WebSocket Server
  -> NoneBot2 + OneBot V11 Adapter（唯一 QQ 网关）
       -> FastAPI ASGI 应用
            -> /onebot/v11/* 事件与平台 API
            -> /、/access WebUI
            -> qq_nonebot 薄网关
                 -> Core CommandService / MessagingPort
                      -> 来源、活动、市场、调度应用服务
                           -> SQLite / HTTP 数据源
```

NoneBot 当前驱动组合为 `~fastapi+~websockets`：WebSocket 客户端连接 NapCat，FastAPI 驱动承载本机管理 API。当前由一个进程同时承载 QQ gateway、worker 和 WebUI，Windows/Linux 入口见 [`UV-USAGE.md`](UV-USAGE.md)；模块边界仍保持可拆分。

## 模块职责

下表是目标职责划分，尚未按目录完整落地。当前代码主要位于 `ff14_core/`、`ff14_web/`、`ff14_app/` 和 `plugins/qq_nonebot/`；应用服务仍直接依赖具体 `StateStore`，后台循环由 `ff14_app.workers.ApplicationWorkers` 承载，网关只注册生命周期。恢复开发时发现的约束差距见 [`TODO.md`](TODO.md#恢复开发核查2026-10-30)。

| 模块 | 负责什么 | 不应依赖什么 |
| --- | --- | --- |
| `domain` | 区域、文章、活动、市场结果、群策略、调度意图和投递状态。 | NoneBot、OneBot、FastAPI、SQLite 行对象。 |
| `application` | 命令分派、源同步、活动查询、市场推荐、通知规划、投递与重试。 | QQ 事件类、HTTP 路由和具体数据库驱动。 |
| `infrastructure` | SQLite 仓储、HTTP 客户端、解析器、缓存、时钟和调度唤醒器。 | NoneBot Matcher 和 QQ 消息段。 |
| `plugins/qq_nonebot` | 将 OneBot 事件转成应用请求，实现 QQ 回复和主动发送端口。 | 数据源解析、分类规则和表结构。 |
| `adapters/http` | FastAPI DTO、管理路由和 WebUI。 | NoneBot 事件类型。 |
| `ff14_app` / `runtime` | 初始化配置和依赖，注册启动/停止钩子，运行 source poller 与 dispatcher。 | 具体活动分类规则。 |

统一入口由 NoneBot driver 生命周期承载来源轮询、活动摘要 tick 和维护提醒 tick；任务在启动钩子创建、关闭钩子取消，插件导入阶段不启动外部工作。WebUI 与 NoneBot 使用同一 `CoreApplication.store`。

当前：新闻发现、正文复查和三流投递由独立唤醒任务承载。列表周期采用 [产品确认的可配置间隔](GOAL.md#1-官方资讯订阅)，每秒读取持久设置并判断是否到期；没有停机刻度回放。正文队列每分钟评估，优先未抓取或最久未尝试的文章，正常正文按24小时复查；预查独立按摘要窗口准备。缩短列表周期不会全量重抓国服历史正文。

当前 P3 运行设置：列表周期、复查提前量及新鲜度阈值由本机 WebUI 管理并持久保存为应用级 SQLite 设置；群级开关、区域、分类和日程仍保存在各群策略中。`.env` 继续只保存连接配置。应用服务读取设置并编排有界工作，生命周期只负责唤醒和停止。

## QQ 网关边界

### 入站

QQ 网关把平台事件转成统一请求，至少包含：

```python
@dataclass(frozen=True)
class CommandRequest:
    target: TargetRef
    actor_id: str
    actor_role: str | None
    text: str
    reply_context: object | None
```

`CommandService` 负责 `ff14` 命名空间、别名、参数、群功能开关和权限。NoneBot Matcher 不为每个业务命令复制一套规则，只负责识别 FF14 请求并交给 Core。

### 出站

应用通过 `MessagingPort` 发送，不直接持有 NoneBot `Bot`：

```python
class MessagingPort(Protocol):
    async def reply(self, request: CommandRequest, message: OutboundMessage) -> SendResult: ...
    async def send_proactive(self, target: TargetRef, message: OutboundMessage) -> SendResult: ...
```

普通文本是共同基线；图片、Markdown、合并转发等属于 provider renderer 的增强能力。消息长度拆分和降级在 adapter 完成，业务模板不生成 OneBot JSON。

Bot 连接状态单独记录。调度任务到期而 Bot 离线时，delivery 进入可重试状态，不能静默丢失或直接视为发送成功。投递候选区分 `pending`、`sending`、`uncertain`、`sent`、`failed` 和 `superseded`；只有 provider 返回有效消息 ID 的确认结果才进入 `sent`。超时、未知异常、缺失消息 ID 或发送期间中断都进入 `uncertain`，不自动重发。普通文字在 OneBot adapter 中使用文本消息段，避免把来源正文中的 CQ 语法执行为平台指令。

## 命令接口

NapCat 路径保留 `ff14 <子命令>` 命名空间，避免与同一 QQ 账号上的其他机器人业务冲突。初始用户命令包括：

- `ff14 活动 [范围]`：按当前群策略查询活动；
- `ff14 价格 <物品> [数量/品质/范围]`：查询市场；
- `ff14 状态`：仅 Bot 所有者查看源、调度和通道状态。

数据源刷新、候选解析和投递测试属于管理员诊断命令，不是长期用户接口。根命令和子命令别名由 Core 注册表管理；修改根命令后允许要求重启，不把热重载作为产品能力。

## 资讯与活动模型

文章不等于活动；同一篇文章可以提出零到多个时间断言，同一活动也可能由多个文章描述。

```text
EventSource -> SourceDocumentVersion -> EventAssertion -> CanonicalEvent
                                                      -> Announcement
CanonicalEvent -> NotificationCandidate -> Delivery
```

### 稳定身份

- 来源文档身份使用 `(source_id, provider_document_id)`；标题不是主键。
- 文章的每个内容版本保存 hash、抓取时间和原始 payload 或安全引用。
- 当前：`article_version` 保留逐篇原始观察序列和独立机械比较表示，后者覆盖 [产品指定字段](GOAL.md#1-官方资讯订阅)。`article_probe` 保留未变化抓取的尝试时间；正文观察在解析/路由完成前不具备公告领取资格，中断后由正文队列恢复；规范化升级先建立基线，提供只读预览。旧详情保留在兼容表中，缺少的历史标题与比较字段不反填。
- 活动区域是结构化 `region`，例如 `cn`、`global-na`、`global-eu`、`global-jp`、`global-oc`。
- 分类使用稳定代码作为数据库身份；中文标签只用于显示，重命名不能丢失群策略。
- 断言保存标题、时间、时区、分类、URL、提取方法、parser 版本、置信度和证据片段。

### 更新与退化

1. 新内容先写入来源快照，再运行解析器。
2. 拉取失败保留最后成功快照。
3. 已知活动重新解析为零条、时间突然大幅变化或 schema 异常时，保留最后有效规范活动并标记退化，不能立即删除。
4. 仍在进行或未来结束的长周期活动必须定期复查原始文章，不能只刷新最新 N 篇文章。
5. 时间更新或取消只重建未来且未投递的通知候选；delivery 历史不可变。
   当前国服只实现时间更新替换；解析为零条不解释为活动取消，因为现有国服公告没有已开启活动取消记录。
6. 初期可在单一官方来源内使用保守身份规则；接入多个来源后才引入复杂的自动合并和人工冲突处理。

具体来源和接口字段只在 [`DATA-SOURCES.md`](DATA-SOURCES.md) 维护。

## 三类主动消息

主动消息不能只靠一个全局“加入日历”开关表达。

| 消息流 | 触发 | 关键状态 |
| --- | --- | --- |
| 活动摘要 | 每群日程到期 | 区域、活动分类、进行中/即将开始/即将结束窗口。 |
| 新公告 | 来源出现新文档或检测到文章内容变动 | 启用水位线、公告分类、对照版本、内容版本和去重键；判断范围见 [产品规则](GOAL.md#1-官方资讯订阅)。 |
| 维护提醒 | 维护公告或相对时间到期 | 独立开关、提前量、时间变化；国服当前不以解析为空判定取消。 |

同一活动先作为新公告推送、之后进入每日活动摘要是预期行为。相同消息流、相同目标、相同业务窗口重复发送才是重复投递。

当前 P3 公告流程：文章观察与待通知意图持久化；群级对照基线、最后确认发送版本和待发送版本分别保存。汇总/离线补发保留批次成员与分页进度，同一通知资格只能由一个发送批次消费；只有确认成功后才推进相应成员的已发送基线，未知结果不重打包。未开始发送的版本可合并，已开始投递的批次保持冻结。首发与后续变动分别保存资格时间；同一比较内容的格式观察沿用原资格，过期意图不为真正的新变化继承旧首发时间。冻结页覆盖的相同内容不再打包；真实后继意图持久保存，待原页结果明确后规划，并以最后确认发送版本渲染差异。具体行为与验证切片见 [P3 实施规格](.scratch/p3-news-and-global/spec.md)。

## 群与策略模型

`targets` 使用内部 UUID，并保存 `provider`、`target_type`、`provider_target_id`、显示名和启用状态。所有者 QQ 号保存在本机 SQLite 的应用设置中。WebUI 是两者唯一的管理入口；运行中的命令服务每次授权检查都读取数据库，因此变更不依赖重启。业务表引用内部 UUID，不把普通 QQ 群号传播到领域模型。

群策略至少包含：

- 功能：活动摘要、新公告、维护提醒、市场查询；
- 各消息流独立的区域集合和稳定分类代码；
- 用户友好的日程定义和时区；
- 活动摘要时间窗口（开始前为 7/14/30 天档，结束前为小时档）；
- 市场默认服务器、大区、品质、跨大区收益与新鲜度阈值；
- 普通群员活动查询与维护查询的独立授权，默认关闭，与主动订阅无关；诊断权限只认数据库中的 Bot 所有者身份，不信任平台群主角色。

当前三流分别保存区域集合，首批实现国服与 NA。旧摘要/维护的 `region` 迁移为单元素 `regions_json`，兼容字段仍保留；显式窗口和已发送日期键保持不变。公告按区域与分类保存独立 scope epoch、生效点与群级基线；空集合表示不订阅，新增范围不重置持续范围。

移除群使用归档并关闭策略，不删除 target、通知候选或投递历史；重新添加沿用内部身份，但需要重新授权查询和主动订阅。

Bot 所有者通过 WebUI 管理策略。常规界面保存“每天 20:00”“结束前 24 小时”等用户意图，不要求填写 Cron。

## 调度与投递

数据库保存用户意图、通知候选和投递账本。调度库或 asyncio loop 只负责唤醒 dispatcher，不是真实状态来源。

投递流程：

```text
SchedulePolicy/Event change
  -> NotificationPlanner
  -> NotificationCandidate（带业务幂等键）
  -> Dispatcher 原子领取候选、持久写入 in_flight attempt
  -> MessagingPort
  -> 完成 DeliveryAttempt / provider_message_id
```

- 每个目标独立失败和重试；一个群发送失败不能阻断其他群。
- 幂等键覆盖目标、消息流、业务对象版本和计划窗口。
- 手动预览调用同一个 query 和 renderer，但不创建成功 delivery。
- 当前查询将来源健康与逐活动 `EventFreshness` 分开返回；阈值从应用 SQLite 设置读取。手动保留旧内容，自动规划依 [产品规则](GOAL.md#2-活动状态摘要)排除过旧/未核实条目，正常空结果不产生通知。
- 当前由 `ReviewCoordinator` 按计划窗口预留复查，按文章合并，最多4个网络请求并发、周期队列按来源交错选择后限制为40篇（保留各来源内部顺序，避免持续失败来源占满总预算）；到点不等待 HTTP。默认提前5分钟，可在本机调整：该预算通常可容纳约4批最慢国服请求，超过预算的请求按本次未核实退化。正文请求有超时和有限重试，不进入QQ adapter或领取事务。
- 当前核实状态绑定活动所依据的有效内容 hash：来源列表成功或其他文章成功不推进它，正文未变但已成功核实可推进；解析退化保留原有效版本与核实时间。预查按计划窗口恢复，晚到结果不改写已开始投递的批次；全量过旧暂停提示以持久故障周期去重，恢复结束该周期。
- 发送前原子复核目标、消息流、通知子类型、实际覆盖区域和业务有效期；未开始摘要另检查成员版本及年龄；停止策略时保留在途 attempt 并阻止后续重试。
- 统一入口先取得同一数据目录的进程锁，再恢复上次遗留的 `sending`/`in_flight` 为 `uncertain`；数据库事务领取同时避免重复唤醒争抢同一候选。它不能替代远端幂等，因此不宣称网络超时下的恰好一次投递。
- 所有者通过 WebUI 关闭未知结果，追加 `delivery_review`，保留原 attempt；当前不提供自动回放。
- 活动摘要按群本地日期去重，修改发送时刻不会创建当天第二批；已开始投递的批次冻结，尚未发送的批次可重建并清理多余分页。
- 维护观察的变化类型与语义版本持久化，避免观察写入后中断导致漏通知；无时间变化的 parser 版本更新不创建新通知。提前量窗口内的公告可覆盖提醒，提前量窗口外已发送的公告不永久抑制后续提醒。
- 单实例阶段只运行一个 dispatcher；运行锁防止误启动第二个应用，不引入多实例选主系统。

## 市场查询

`MarketQueryService` 依赖两个端口：本地 `ItemCatalogProvider` 负责中文名称和别名，`MarketDataProvider` 负责 Universalis 数据。

推荐逻辑使用实际数量的挂单组合总价。默认比较本大区服务器；跨大区推荐同时要求绝对收益、比例收益和数据新鲜度达标。原始上传时间、范围、NQ/HQ 和没有数据的原因必须保留到输出 DTO，renderer 不能把缺失数据写成零价格。

## 存储

初期继续使用 SQLite。当前实现使用标准库 `sqlite3`、可重复的增量建表/字段迁移、WAL 和
SQLite Backup API；每次 schema 版本变化会先生成 `.pre-migration.bak` 以及不覆盖的版本化备份，并可通过完整性与
外键检查验证恢复副本。schema 16 的历史处理继续保留；当前P3代码为 schema 22，新增设置、文章比较版本、公告scope/基线/批次、区域集合、核实与预查/故障周期。预演、备份和恢复见 [`UV-USAGE.md`](UV-USAGE.md#p3-schema-22-升级与恢复)。SQLAlchemy/Alembic 是可选实现，不是产品前置条件。

目标数据范围：

| 范围 | 核心记录 |
| --- | --- |
| 来源 | source、document version、health、cursor。 |
| 活动 | assertion、canonical event、override。 |
| 群策略 | target、feature policy、schedule。 |
| 投递 | notification candidate、delivery attempt。 |
| 市场 | item catalog、alias、quote cache。 |
| 运维 | setting、audit log、schema version。 |

当前 SQLite 启用 WAL、外键和 `synchronous=FULL`，迁移前自动备份；定期备份仍需部署侧安排。只有多实例、高可用、多个远程 writer 或实际数据规模证明有必要时才迁移 PostgreSQL。

## WebUI

WebUI 是 Bot 所有者的管理界面，不面向群管理员。当前服务端渲染月历可以继续使用；只有复杂交互证明有必要时才引入 React/Vite。

首要页面顺序：

1. 活动日历和来源证据；
2. 群目标与三类消息策略；
3. 投递预览、历史和失败处理（当前已有近期历史、未知结果关闭，尚无回放界面）；
4. 市场默认值与缓存；
5. 数据源健康和系统设置。

服务未实现认证前只监听 `127.0.0.1`。未来开放到局域网或公网前必须增加认证、CSRF 防护、审计和 secret 管理。

## 部署与安全

1. 应用只连接既有 NapCat，不安装、配置、启动或登录 NapCat。
2. NapCat WS 只通过可信内网/VPN 访问，使用长随机 token 和网络 ACL，不暴露公网。
3. secret 不存数据库、不写入版本库，配置示例不得包含真实账号和 token。
4. 生产运行使用进程监督、结构化日志、自动重启和 SQLite 备份。
5. 同一测试群只能由一套 QQ 网关处理，否则 NapCat 广播会导致双回复。
6. NapCat 属于非官方 QQ 自动化通道，部署文档必须保留账号风险说明，不提供规避平台检测的策略。
7. Koishi 不属于本项目运行时；只读历史边界见 [`ARCHIVE-KOISHI.md`](ARCHIVE-KOISHI.md)。

P3离线历史证据见 [验收报告](.scratch/p3-news-and-global/offline-acceptance.md)，2026-11-05已完成授权范围内的 [受控真实验收](.scratch/p3-news-and-global/controlled-acceptance.md)。真实消息经唯一NoneBot网关与现有Core工作流投递；验收只使用隔离库和副本迁移，正式库仍为schema16，正式部署与长期调度尚未验收。
