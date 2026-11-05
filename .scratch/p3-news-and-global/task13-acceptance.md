# 任务13修复与离线重新验收

> 后续状态：任务12已另获授权并完成受控范围，最新137项测试和真实验收证据见 [受控报告](controlled-acceptance.md)。下文保留任务13当时的执行范围。

日期：2026-11-05（Asia/Shanghai）。范围为 [任务13](issues/13-review-corrections.md) 的R1–R4，以及任务11的离线重新验收。没有联网核验上游、读取连接凭据、启动正式应用、迁移正式库或向真实群发送消息；任务12仍未开始。

## 修复结果

| 发现 | 原因与修复 | 回归 |
| --- | --- | --- |
| R1 | 来源列表原先先拼接再截前40，持续失败的国服占满预算。现在按来源交错选择后再限制总量，保留各来源内部复查顺序，总量40、并发4均不变。 | 三轮国服持续失败与NA新/旧详情共存；三来源全满及单来源/空队列；真实临时SQLite与假HTTP验证NA首次正文及旧活动核实成功。 |
| R2 | HTMLParser把自闭合VOID标签分派为start/end，end错误减少wrapper深度。现在VOID结束标签不改变正文深度。 | `<img>`/`<img />`、`<br>`/`<br />`/`<br></br>`完整正文一致；经过真实详情服务后活动候选、比较指纹一致且无伪变动；wrapper外文字不进入正文。 |
| R3 | 未确认首发一直借用旧发布时间，过期后真实变动仍被丢弃。现在首发仅在自己的72小时内取得资格；后续真实变化使用对应内容观察时间，过期可编辑意图退出合并。 | 初始资格先前已标过期/本轮才过期两种路径；更新合并及明确离线重试保留原资格时间；纯格式观察不复活已过期变动。 |
| R4 | 原始version_id不同被当作新的待发内容，忽略冻结页已覆盖的相同比较内容。现在冻结内容与后继变化分开处理；真实后继意图持久保存，但等待原页结果明确后才打包。 | 冻结多页遇到纯样式变动只发一次；B/C多次真实变动合并、资格时间不延长，原页确认后按最后成功版本呈现差异；历史失败A不抑制后续真正的A→B→A。 |

实现只修改 `ff14_app/reviews.py`、`ff14_core/na_events.py`、`ff14_core/news.py`，新增 `tests/test_p3_review_corrections.py`。没有新依赖、schema或持久字段；schema仍为22。旧意图、版本和attempt历史保留，资格在正常规划时重新判断，不执行正式库回放或批量重解析。

## 先失败后通过的证据

新增回归在修复前实际运行：最初7项用例产生12个失败断言（含subTest），分别命中NA饥饿、正文截断、近期更新漏发、冻结分页重复以及窗口延长；[修复前输出](review-evidence/task13/regressions-before.log)。随后加强真实存储/假HTTP集成回归，使用接手时的调度器只读快照在独立测试进程验证，明确因NA没有正文调用失败；[集成修复前输出](review-evidence/task13/integration-before.log)。这一步没有覆盖或回滚工作区代码。

最小断言排除了真实网络和睡眠；调度集成的线程使用正常asyncio唤醒环境。修复后同一测试入口进入完整suite，最终9项新回归及全部既有测试通过。没有残留调试插桩。

原审查复现命令也已重跑：

```bash
.venv-linux/bin/python .scratch/p3-news-and-global/review-evidence/news_repro.py
PYTHONPATH=. .venv-linux/bin/python .scratch/p3-news-and-global/review-evidence/fairness_repro.py
.venv-linux/bin/python .scratch/p3-news-and-global/review-evidence/body_repro.py
```

[修复后输出](review-evidence/task13/reproductions-after.log)：近期变化应发1条/实际1条；冻结文章应发1次/实际1次；三轮调用国服114、NA新闻3、NA Topics 3，合计仍为120；图片后的Event Period完整保留。

## 实际验证命令

执行环境：Linux `.venv-linux`，Python 3.14.9、uv 0.12.4。

```bash
PYTHONPATH=tests .venv-linux/bin/python -m unittest test_p3_review_corrections test_p3_news test_p3_na_freshness test_p3_preflight test_p3_workers -q
UV_CACHE_DIR=/tmp/ffxivbot-uv-cache timeout 180 bash scripts/check-linux.sh
bash -n scripts/start-nonebot-dev.sh scripts/check-linux.sh
.venv-linux/bin/python -m compileall -q ff14_app ff14_core ff14_web plugins tests
git diff --check
```

- 相关模块32项通过：[targeted.log](review-evidence/task13/targeted.log)。
- 全套136项通过，锁文件33包解析检查、已安装30包兼容检查通过：[check-linux.log](review-evidence/task13/check-linux.log)。
- 启动脚本语法、Python编译及差异空白检查通过。原迁移/恢复、权限、未知投递、运行锁和隔离生命周期回归随完整suite通过。

任务11据此重新完成本轮离线验收；这是对已列R1–R4的修复和既有回归确认，不是任意HTML/语言全覆盖或无其他缺陷的保证。真实运行和任务12仍须单独授权。

文档校验：30份关联文档的200个本地链接/锚点有效；任务13六项验收均勾选，任务11已重新完成，任务12仍为not-started。另将既有长差异200字符截断的展示改进记录至TODO的P5；本轮只修R1–R4，不调整QQ消息长度策略。
