# FFXIVBot 调查归档

状态：归档研究，整理于 2026-08-14 至 2026-08-16。当前产品决策以 [`GOAL.md`](GOAL.md) 为准，架构以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，数据源事实以 [`DATA-SOURCES.md`](DATA-SOURCES.md) 为准。

本文保留形成决策所需的证据和被否决方案，不再承担“当前设计文档”的职责。依赖版本、平台规则和项目活跃度都是调查时快照，实施前需要重新核验。

## 决策记录

1. 后端与业务 Core 使用 Python，产品作为独立应用运行；不把核心业务封装成可分发的 NoneBot 插件。
2. 既有 NapCat 继续提供 OneBot 11，QQ 网关统一为 NoneBot2 + OneBot V11 Adapter。
3. NoneBot 本地插件只做事件/API 转换。新闻、活动、市场、策略、调度、投递和 WebUI 不依赖 QQ 框架。
4. 不自行实现原始 OneBot WebSocket 客户端。已有 adapter 能覆盖连接与事件协议，自研不会带来产品差异。
5. 当前使用 NapCat 个人号接入并接受其非官方平台风险，不实现规避风控。QQ 官方机器人已注册个人开发者账号，但适配延期。
6. Bot 所有者通过本机 WebUI 管理少量群；每群独立开关功能，但群管理员暂不自助配置。

## QQ 框架评估

### 方案比较

| 方案 | 能力与代价 | 结论 |
| --- | --- | --- |
| NoneBot2 + OneBot V11 Adapter | 文档和生态更成熟；Core、driver、adapter、plugin 职责清晰，官方 OneBot V11 adapter 可承接事件与动作 API，FastAPI 与 WebSocket 客户端 driver 可组合。 | 目标 QQ 网关；使用私有本地薄插件，不把 FFXIVBot 变成通用插件。 |
| 自行实现 OneBot WebSocket | 表面只需收发 JSON，实际仍要负责鉴权、心跳/重连、action echo 关联、超时、事件/消息段兼容、连接状态和协议变化。 | 没有差异化价值，排除。仅保留框架无关的 `MessagingPort`，便于未来换 adapter。 |
| Koishi | TypeScript 生态和既有部署可用，但会形成第二套语言/进程边界，且旧 `ff14calendar` 不能提供可复用的可靠性基础。 | 不属于本项目运行时或迁移目标。 |

NoneBot2 文档支持“驱动负责底层网络、适配器负责协议、插件负责事件处理”的拆分。目标组合为 `~fastapi+~websockets`：前者可承载现有本机 HTTP 管理入口，后者作为 WebSocket 客户端连接既有 NapCat。具体环境变量和依赖名必须在 P0 按当时文档及 adapter 版本锁定。

### 独立应用还是 NoneBot 插件

“使用 NoneBot”与“产品是 NoneBot 插件”不是同一件事。本项目包含持续轮询、SQLite、FastAPI WebUI、投递账本和以后可能增加的其他消息 provider，生命周期大于一个 matcher 插件。把整个 Core 放进插件会让领域服务依赖热重载、Bot 在线状态和框架全局对象。

因此采用独立 Python 应用：应用 runtime 拥有 Core、worker、WebUI 和存储；同仓库内的私有 NoneBot 插件只注册 handler，将事件交给 `CommandService`，并实现 `MessagingPort`。它无需发布到插件商店，也不承诺兼容任意第三方 NoneBot 实例。

## NapCat 与 QQ 官方机器人

### 当前 NapCat 路径

NapCat 是外部 OneBot V11 provider；FFXIVBot 通过 NoneBot2 的 WebSocket driver 连接既有服务，不安装、配置、启动或登录第二个 NapCat。连接 secret 应留在本地配置或环境中，WS 只经可信内网/VPN 和 ACL 暴露。

NapCat 是个人 QQ 自动化的非官方实现。NoneBot2 只改善应用边界和生态依赖，不会降低 QQ 账号侧的封控风险。项目不研究模拟人工、绕过检测或其他规避措施。

### 官方机器人延期原因

官方路径的优势是平台支持、权限边界明确，不依赖个人 QQ 客户端自动化；代价是申请/审核、事件和主动消息能力、群场景、消息格式以及链接策略均受开放平台当期规则约束。特别是国际服 Lodestone URL，存在域名安全校验、白名单或客户端拦截风险，不能把“群消息一定能携带可点击原文链接”写成跨 provider 契约。

独立应用和 `MessagingPort` 让未来增加官方 adapter 在技术上可行：数据抓取、分类、群策略与投递候选可以复用，官方事件模型、目标身份、授权、主动消息配额和 renderer 需要单独实现。开发者账号已经注册不等于当前具备所需权限；正式适配时必须重新核验腾讯当期文档和实测行为。

## 旧 `koishi-plugin-ff14calendar` 调查

以只读方式分析了 <https://github.com/FreezeNovo/ff14calendar>，调查时 HEAD 为 `8e9d902`。该仓库是独立归档产品，只用于解释历史交互，不是 FFXIVBot 的代码来源。

### 已实现行为

- 包名 `koishi-plugin-ff14calendar`，版本 `0.0.7`；主要业务集中在 `src/index.ts`。
- 把 `webcal:` URL 改写为 `https:`，用 `axios` 下载并以 Node `ical` 解析 `VEVENT`。
- 把事件分为“正在进行”和“未来”，按开始时间排序，未来活动默认最多显示 3 个。
- 模块级 `later.setInterval()` 向多个 `platform:id` 目标推送；`webcal.today`/`最近活动` 返回相同摘要。
- 支持代理、自定义摘要模板和 Koishi Console 配置，但 `dist` 只有极小扩展页，不构成完整管理 GUI。

### 不可继承的问题

| 问题 | 证据与影响 |
| --- | --- |
| Cron 语义可能错误 | `later.parse.cron(config.cronTime, true)` 声明包含秒，而默认值是五段 `0 8 * * *`；有被解释为每小时第 8 分钟的风险。 |
| 时区隐式 | 调度和 `toLocaleString()` 依赖主机时区，无法可靠处理国服/国际服。 |
| 全局内存任务 | 单个模块变量保存 timer，实例/热重载互相覆盖，重启丢失状态。 |
| 无投递账本 | 重启、慢请求或热重载可能重复发送；目标失败未隔离。 |
| 无最后有效快照 | 每次现拉唯一 ICS，没有条件请求、缓存、退避、健康状态或退化保护。 |
| ICS 语义不全 | 没有完整处理重复规则、`UID`/`SEQUENCE`、时区、版本和取消。 |
| 渲染与目标脆弱 | `String.replace` 只替换首个占位符；目标用 `split(':')`，消息长度与群/私聊权限未建模。 |
| 构建与依赖陈旧 | 调查时只有 10 个提交，集中在 2025-06；`later` 长期未更新，`node-cron` 留在依赖但未使用，缺少测试和可重复构建。 |

该插件唯一值得保留的是需求观察：紧凑摘要、正在进行优先、手动查询和多目标是用户能理解的交互。代码、配置、命令、Cron、模板和 ICS 来源都不迁移，详见 [`MIGRATION.md`](MIGRATION.md)。

## 外部 FF14 实现

以下结论用于避免重复研究，不表示已核对完许可证或允许复制源码。

| 项目 | 已有能力 | 本项目取舍 |
| --- | --- | --- |
| [`koishi-plugin-ffxiv-bot-hcn`](https://github.com/LeeJSwit-HCN/koishi-plugin-ffxiv-bot-hcn) | Universalis 查价、历史、服务器范围、HQ 与 QQ 命令。 | 维护陈旧且有重复消息问题，不依赖、不迁移。 |
| [`TataruBot2`](https://github.com/aaron-lii/TataruBot2) | Universalis 查价、HQ、服务器/DC 别名、更新时间，另有日历与攻略功能。 | 只参考 NQ/HQ、范围和新鲜度的用户表达，不复制旧框架或 XIVAPI v1 链路。 |
| [`TataruBot2-qq`](https://github.com/aaron-lii/TataruBot2-qq) | QQ 官方机器人形态的适配镜像。 | 提交和维护有限，不作为官方 adapter 基础。 |
| [`pamo-nonebot-ff14Tools`](https://github.com/opaup/pamo-nonebot-ff14Tools) | Universalis/Cafemaker 查价、趋势、历史和部分 FF14 工具。 | 可参考趋势展示；不复用陈旧依赖或关闭 SSL hostname 校验的实现。 |
| [`Universalis`](https://github.com/Universalis-FFXIV/Universalis) | 持续维护的众包市场 API，提供中国区/DC、挂单、成交、聚合和上传时间。 | 唯一市场报价上游；本项目负责中文物品目录、缓存、新鲜度和购买决策。 |

公开实现已经证明查价的基础交互并不稀缺。FFXIVBot 的差异应落在官方资讯时间证据、持久投递幂等、逐群策略，以及考虑整组挂单、数据年龄与跨大区操作成本的购买建议。

## 调度方案调查

| 方案 | 调查结论 |
| --- | --- |
| `later`、`node-schedule`、单纯 `node-cron` | 内存任务或 Cron 表达式本身不能保存用户意图、业务版本和投递结果；旧插件已经暴露时区和重复投递风险。 |
| Bree | 有 worker 隔离和并发控制，但仍不提供本项目的领域投递账本，对单机 Python 应用过重。 |
| BullMQ | 延迟、重试和队列能力成熟，但需要 Redis；当前一到两个群没有引入价值。 |
| Agenda | 可用 MongoDB 持久任务，仅为调度引入 MongoDB 不合理。 |
| APScheduler | Python 生态中的可选唤醒器；即使采用，也不能把其 job store 当作通知事实来源。 |
| 周期性 asyncio worker | 对单实例 SQLite 足够简单，可定期查询到期候选；仍需数据库租约/状态转换防止重入。 |

最终不绑定具体调度库。数据库中的 schedule policy、notification candidate 和 delivery attempt 才是真实状态；库或 loop 只负责唤醒 dispatcher。出现多实例 writer 后再评估专用队列和 leader 选举。

## 参考

- [NoneBot2 文档](https://nonebot.dev/docs/)
- [NoneBot OneBot V11 Adapter](https://onebot.adapters.nonebot.dev/)
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)
- [Lodestone News](https://na.finalfantasyxiv.com/lodestone/news/)
- [国服新闻中心](https://ff.web.sdo.com/web8/index.html#/newstab/newslist)
- [Universalis API](https://docs.universalis.app/)
