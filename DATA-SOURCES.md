# FFXIVBot 数据源契约

状态：当前数据源边界，文档更新于 2026-11-04。本次已对国服与NA公开接口重新作有界只读核验，证据见文末。产品目标见 [`GOAL.md`](GOAL.md)，模块与存储设计见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

本文只记录上游能提供什么、哪些字段可信、如何判断退化。消息策略、群配置和技术栈不在这里重复定义。

## 共同规则

- 来源文档使用 `(source_id, provider_document_id)` 标识；标题、分类标签和 URL 都不能单独充当稳定主键。
- 每次成功抓取保留抓取时间、内容 hash、原始 payload 或可重放引用；解析结果保留 parser 版本和证据片段。
- 文章发布时间只表示文章发布，不表示活动开始。只有正文中的明确时间证据才能生成活动断言。
- 拉取或解析失败保留最后成功快照。重新解析得到零条活动不能直接删除仍有效的旧结果，而应标记来源或解析器退化。
- 来源健康至少记录最后尝试、最后成功、连续失败、HTTP/schema 错误摘要、最新文档时间和数据年龄。
- 未文档化接口必须有离线 fixture 与契约测试；上游字段变化先进入退化状态，不能把空响应当成“所有活动已结束”。
- 支持来源返回的 `ETag`/`Last-Modified` 条件请求；`304 Not Modified` 只更新健康状态，不覆盖旧快照或文档。
- HTTP 传输统一设置超时、代理入口、标识性 User-Agent，并仅对超时、429、408 和 5xx 做有限带抖动退避。

## 国际服 Lodestone

### 发现入口

```text
https://na.finalfantasyxiv.com/lodestone/news/
https://na.finalfantasyxiv.com/lodestone/news/news.xml
https://na.finalfantasyxiv.com/lodestone/news/topics.xml
```

`news.xml` 与 `/lodestone/news/topics.xml` 分别发现普通新闻和Topics。已观察到的 Atom 字段包括稳定文章 ID/URL、标题、分类、UTC `published`/`updated`、HTML 内容、链接和图片。这些字段适合文章发现和版本判断，不构成原生活动 schema。

历史调查中的 `topics.xml` 路径不准确；2026-11-04 实测的独立Topics入口、稳定身份与覆盖限制见文末复核，不能沿用旧的“同一feed”判断。

### 正文提取

1. 按 Atom ID 识别文章身份，使用内容 hash 检测所覆盖内容的版本变化；hash 变化不代表语义上发生实质更新；
2. 保存原始 HTML，再生成用于分类和时间解析的规范文本；
3. 识别正文中的 `Date & Time`、日期范围和明确时区，保留原始时区文本与 IANA 时区换算假设；
4. 允许一篇文章生成零到多个活动断言；仅有日期时保留 `day` 精度，不伪造小时；
5. Maintenance 进入独立维护流，Topics/Notices 等上游分类只用于路由，不能单独证明存在可提醒活动。

轮询应使用条件请求（上游支持时）、标识性 User-Agent、超时和带抖动退避。检查周期见 [产品规则](GOAL.md#1-官方资讯订阅)，不代表上游 SLA。

## 国服官方新闻中心

正确的人类入口：<https://ff.web.sdo.com/web8/index.html#/newstab/newslist>。

### 已观察接口

```text
GET https://cqnews.web.sdo.com/api/news/newsList
  ?gameCode=ff
  &CategoryCode=8324,8325,8326,8327,5309,5310,5311,5312,5313
  &pageIndex=N&pageSize=N

GET https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=ARTICLE_ID
```

`newsList` 响应已观察到 `PageCount`、`TotalCount`、`RecordCount` 和 `Data[]`。列表项包含 `Id`、`CategoryCode`、`Title`、`Summary`、`PublishDate`、`HomeImagePath`、`OutLink`、`Articletype` 等字段；`newsDetail` 还包含 HTML `Content`。`Id` 是此 provider 内的文档身份。

当前服务端 GET 不需要浏览器、token、签名或时间戳；CORS 与后端调用无关。该 API 来自官网 SPA，但没有公开契约、分类代码字典或 SLA，因此分类代码保存为原始元数据，不能靠猜测写死含义。

仓库中的 `tests/fixtures/china_news_list.json`、`china_news_detail_392085.json` 和
`lodestone_news.xml` 是离线契约 fixture。`Data` 为空、记录缺少 `Id`、详情缺少非空
`Content` 或 Atom 根节点/entry 结构异常时视为来源退化，保留最后有效快照。

### 抓取与时间规则

- 当前列表读取前5页，保留已发现文章，不以一次窗口穷尽历史；置顶与服务端页大小行为见本次复核。5页是当前有界实现，不是上游契约或完整回溯保证。
- 新增或变化的文档抓取详情；仍在进行或未来结束的长周期活动必须另行定期复查，不能只看最新 N 篇。
- 国服无明确说明的日期按 `Asia/Shanghai` 解释；原文中的其他时区优先。
- parser 需要覆盖 `至`、`~`、`-`、省略年份、`24点` 和一文多活动，并对跨年补全年份保持保守。
- 时间不完整或歧义时保留文章并进入待审核/退化状态，不臆造活动日期。

## 补充来源

### 官方活动专题页

从官方新闻文章链接到的专题页可以补充时间和规则，但页面结构各异，尚未发现统一活动 API。只有已知页面类型才启用专用 parser；未知页面保留链接，不依赖浏览器抓取作为正常同步路径。

### 社媒与社区日历

微博、Bilibili、小红书、抖音、快手、官方论坛和石之家可供人工核验，但目前没有确认一个可长期、免授权依赖的公共活动 feed。它们不作为官网故障时的隐藏备份，adapter 如存在应明确报告 `unsupported` 或 `manual_only`。

社区 ICS/WebCal 当前延期，不属于首批数据源。未来若启用，必须记录 URL、维护者、信任标签和健康状态，正确处理 `UID`、`RECURRENCE-ID`、`SEQUENCE`、`DTSTAMP`、`TZID` 与全天事件；第三方日期不能无声覆盖冲突的官方证据。

## Universalis 市场数据

API 文档：<https://docs.universalis.app/>。

### 使用的接口

```text
GET /api/v2/data-centers
GET /api/v2/worlds
GET /api/v2/aggregated/{worldDcRegion}/{itemIds}
GET /api/v2/{worldDcRegion}/{itemIds}?listings=&entries=&hq=&statsWithin=&entriesWithin=&fields=
GET /api/v2/history/{worldDcRegion}/{itemIds}
```

`aggregated` 适合概览、最低单价和统计，不足以生成指定数量的可执行购买方案。数量报价和服务器推荐必须读取当前挂单，按世界、品质与板税口径一致地计算实际可买组合。

FF14 市场板挂单按整组购买。对“购买 N 个”的结果必须同时展示：选中的挂单组、实际买入数量、超出需求的数量和整组总价。不能用最低单价乘 N，也不能假设可以拆分一个挂单。选择最低成本组合时需考虑整组超买，而不是只按单价排序。

### 范围与推荐

- 默认只比较当前大区内的服务器；用户显式要求全区时再查询更大范围。
- 主动跨大区推荐必须同时满足可配置的绝对节省金额、节省比例和最大数据年龄。
- 比较基线与候选必须使用相同数量、品质、税费口径和数据新鲜度规则。
- 推荐结果保留范围、世界 ID、NQ/HQ、挂单时间/世界上传时间和抓取时间；缺少可信时间戳时视为未观测，不参与主动推荐。
- 无挂单、可购数量不足、未观测、数据过旧和上游失败是五种不同状态，不能统一显示为价格 0。

中国区已观察到 `陆行鸟`、`莫古力`、`猫小胖`、`豆豆柴`，地域为 `中国`；API 可接受 `China` 或 `中国`。世界和大区映射从 API 同步并保存 ID，不维护易过期的硬编码表。

Universalis 数据由玩家上传，不是官方实时数据。按其当前公开文档，持续限流为 25 req/s、突发 50 req/s、每 IP 最多 8 个并发连接；实现应使用可识别 User-Agent、批量请求、TTL 缓存、较低并发及针对 429/5xx 的退避。若官方文档变化，以实现时的契约测试和最新文档为准。

### 物品目录

Universalis 接受物品 ID，不提供本项目需要的中文名称搜索。中文名、别名和消歧由版本化的本地 `ItemCatalogProvider` 提供；数据来源、许可和更新周期尚待确定。

检索顺序应为规范化后的精确名称、别名、前缀/词元包含、模糊相似度。多个候选或低置信度时返回消歧列表，不能擅自选中物品。

## P3 契约复核（2026-11-04 实测）

通过带标识 User-Agent 的有界只读 GET 核验，原始最小样本和请求说明见 [fixture 清单](tests/fixtures/p3/README.md)。这是本次实测，与前文历史观察分开：

- 国服列表/详情仍为上述 JSON 形态。pageSize=3 实际返回5条；pageIndex=1返回下一组5条，PageCount=132、TotalCount=657。存在置顶文章，不保证发布时间递减；有限5页扫描只覆盖最近窗口及置顶条目，不能证明穷尽停机期间文章。Id稳定，CategoryCode保留原代码。PublishDate为无offset的北京时间；没有可信更新时间字段。
- NA `news.xml` 本次20篇，分类 Notices/Maintenance 等，含转义的完整HTML content。官网另外声明 **`https://na.finalfantasyxiv.com/lodestone/news/topics.xml`**，此入口返回独立Topics Atom feed（本次20篇）。旧 `/lodestone/topics.xml` 本次返回HTML错误页，不能作为feed。必须同时发现新闻与Topics，按entry稳定Atom ID及逐篇内容比较。
- NA entry的published/updated带Z；本次二者相同，不能推断旧文修改必然重新进入feed。有限feed没有分页或完整历史承诺。news/detail 与 topics/detail 的官方链接可直接GET复查；仅取 news__detail__wrapper 正文，排除导航、脚本与社区内容。
- NA正文有明确PST/PDT，也有仅日期、around、until the end of the performance、缺年或无结束时刻。PST/PDT按明确固定offset解释（-08/-07），不依赖主机时区；仅日期保留day精度，不产生精确维护提醒；不确定区间只保留文章。不能按发布时间补活动起点。
- 这些响应为200，本次未见ETag/Last-Modified；未实测304。保留离线304验证但不宣称接口一定支持。空/缺字段、HTML替代XML、部分分页失败都视为退化并保留旧数据。
- 列表/Atom不保证披露旧文修改，已知活跃文章需直接正文复查；未入扫描窗口且未曾发现的旧文章、同一图片URL下字节变化都存在观测盲区。公开接口可访问不等于SLA。

当前 NA parser 为 `na-time-v1`，只接受已记录标签下的明确起止区间；时间型使用UTC持久化，保留原始PST/PDT与补年假设。只有日期的范围另存 day 精度证据，不转换为精确提醒。详情304要求实际发出逐篇条件头，并与已保存内容版本对应；列表304不刷新活动核实时间。

## P3 受控复核（2026-11-05）

任务12通过既有HTTP transport对国服列表、NA新闻/Topics各作一次发现，并直接复核国服详情与NA活动、维护正文；各列表返回20篇，含标题诊断共9次逻辑GET调用（底层有限重试不能折算为相同数量的实际网络请求）。本次抽样与上述契约相符，不增加历史覆盖或可用性承诺；未观察304，不能声称已在线验证条件响应。计数及范围见 [受控报告](.scratch/p3-news-and-global/controlled-acceptance.md)。

Topics详情包含独立的 `<h1 class="heading__title">Topics</h1>` 栏目标题，后面另有文章h1。正文parser排除该栏目h1并为文章h1重新收集标题，避免将页面导航拼入官方标题并误报更新。公开最小样本及采集说明见 [fixture清单](tests/fixtures/p3/README.md)，修复前失败的离线回归见 `tests/test_p3_live_findings.py`。该补充不扩大时间解析形态或改变稳定来源身份。
