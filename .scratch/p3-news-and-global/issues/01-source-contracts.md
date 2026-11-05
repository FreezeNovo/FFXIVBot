# 01：核验国服与 NA 来源契约并固定样本

Status: ready-for-agent
Category: enhancement
Resolution: completed
Blocked by: None
Spec: [P3 实施规格](../spec.md)
User stories: US01、US02、US05、US08、US16、US17

## What to build

建立可重放的来源证据，使后续实现能够验证“文章是什么、何时发布、正文是否变化、活动时间是否明确”。这是事实核验门槛，不把研究报告当作已经实现 NA 或公告通知。

## 当前入口与边界

已有 OfficialSourceService、国服列表/详情 adapter、NA Atom adapter、SourceTests 及原始 fixture。先读 [数据源契约](../../../DATA-SOURCES.md)、[开发约束](../../../AGENTS.md) 和 [领域术语](../../../GLOSSARY.md)。只针对官方公开读接口核验，不启动应用、读取本地连接配置或接触 QQ。

## 验收条件

- [x] 记录实际核验日期、官方来源链接、请求方式与响应形态，区分本次实测、历史观察和未获证据的假设；写回 DATA-SOURCES，不复制产品策略。
- [x] 核验国服与 NA 的稳定 ID、发布时间/更新时间、标题、正文、图片/链接引用、列表分页及正文获取方式；不能把整份 feed hash 当作逐文章版本身份。
- [x] 保存可公开提交的最小原始样本及来源说明，覆盖普通公告、明确活动时间、维护、无明确时间、仅日期、多活动、跨月/年和适用的时区/夏令时案例；人工变体须标明为合成案例。
- [x] 核查列表是否能发现旧文章修改、有限回溯的覆盖范围、长周期活动正文复查所需入口；不足之处明确记录，不能宣称所有旧文章更新均能即时发现。
- [x] 验证或标明不支持的条件请求、空响应/缺字段和分页退化；不从一次空数据推导合法删除，不把接口当前可访问当作长期 SLA。
- [x] fixture 契约测试验证字段意义与退化结果，默认完全离线；抓取样本的联网步骤和回归测试分离。
- [x] 如发现产品范围无法由来源支持，记录具体冲突并阻塞受影响任务，保留可继续部分，不猜测字段或用机器本地时区补时间。

## 验证与完成证据

运行新增契约/parser 输入测试及相关 SourceTests，记录命令、日期与结果。样本需检查隐私信息与来源可追溯性。不得把尚未实现的业务标为完成；更新本任务 Resolution 和 TODO 中对应契约状态。

## Comments

2026-11-04：未开始。本规格编制未重新请求上游，历史 fixture 不是当前接口仍有效的证明。

2026-11-04 完成：DATA-SOURCES 的P3复核与 tests/fixtures/p3/README.md 记录本次公开只读证据及观测盲区。`PYTHONPATH=tests .venv-linux/bin/python -m unittest test_p3_contracts test_sources -q`：18项通过。跨年/多活动/仅日期/无时区合成输入由07的保守parser测试承接，不声称适用所有自然语言。
