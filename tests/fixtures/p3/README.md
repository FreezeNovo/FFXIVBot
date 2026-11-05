# P3 来源样本

2026-11-04 只读 GET，User-Agent `FFXIVBot/0.2 source-contract-check`，每请求超时25秒。
未携带登录凭据，没有访问 QQ 或正式数据库。样本为官方公开接口返回，保留选中条目的原字段；NA feed 为保留 feed 元信息和少量 entry 的 XML 子集。

- `cn-list.json`、`cn-page1.json`：国服 newsList，gameCode=ff，CategoryCode 为 DATA-SOURCES 所列代码，pageIndex=0/1，pageSize=3。实际 RecordCount=5，页大小未照传入值执行；置顶顺序不等于时间顺序。
- `cn-detail.json`：newsDetail，id=395032，含跨年延期文章。
- `na-news.xml`：`https://na.finalfantasyxiv.com/lodestone/news/news.xml` 的维护、普通公告条目，含 PST/PDT 和跨月区间。
- `na-topics.xml`：`https://na.finalfantasyxiv.com/lodestone/news/topics.xml`，有限时售卖及演出/存档多区间；开始不确定或缺结束时间不能生成精确活动。
- `na-detail.html`：`https://na.finalfantasyxiv.com/lodestone/news/detail/dd452cbe526e23a82298c2ee2ae7f6b80f0e9c85` 的 news__detail article 原始片段。
- `na-topic-detail.html`：`https://na.finalfantasyxiv.com/lodestone/topics/detail/976d4159821fbe701edc301482bd15c1e46ed49c` 的正文 wrapper 原始片段。

仅正文片段入库，排除了页面中的社区角色/用户生成内容。合成 parser 边界案例在测试中显式标注，不能冒充线上文章。条件响应本次200未见 ETag/Last-Modified；304只由离线合约验证，未证明上游长期支持。未测试限流阈值。

2026-11-05受控验收补充：`na-topic-headings.html`为官方Topics详情的两个原始h1最小片段（页内分类标题与文章标题），用于防止将Topics误拼入原文标题；不包含社区内容或连接凭据。
