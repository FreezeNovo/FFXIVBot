# 投递不确定结果与关闭策略后的待发送消息

Status: ready-for-agent
Category: bug
Resolution: completed (2026-11-02; offline verified)

## 修复前事实

2026-10-30 离线核查。`ff14_core/calendar.py` 与 `ff14_core/maintenance.py`
的 dispatcher 在发送结束后才调用 `record_delivery_attempt`，发送异常统一视为可重试。
`ff14_core/storage.py` 的 `list_pending_notifications` 只检查候选 pending 和目标 enabled，
没有重查所属消息流开关，也没有消息过期判断。

使用临时 SQLite 和 fake MessagingPort 复现：第一次记录模拟上游接收后抛出 TimeoutError，
关闭活动策略，再次 dispatch，同一消息仍被第二次接收。没有调用真实 provider。

## 原待确定事项（已由本次修复落实，见 Comments）

明确不确定结果的人工核对/回放方式，以及离线消息的有效期与补发策略。
目标仍遵守 [GOAL.md](../../../GOAL.md) 的持久去重要求；不能把未知结果冒充成功，
也不能仅靠本地幂等键宣称远端恰好发送一次。

## 验收条件

- 在外部发送前持久记录可恢复的 attempt；明确区分未发送、已确认成功、明确失败和未知结果。
- 未知结果不能无条件自动重发；提供受控的后续处理状态和审计记录。
- 活动/维护策略关闭后，既有待发送消息不得继续投递；目标停用和过期消息有明确处理语义。
- 使用可控时钟和 fake provider 覆盖接收后超时、发送后崩溃、重启、逐群失败和关闭策略。
- schema 迁移保留旧候选/attempt，并同步运行恢复说明。

## Comments

本次仅核查和登记，未修改投递逻辑或回放真实账本。


2026-11-02：已实现发送前 attempt、未知结果暂停及人工关闭审计、窗口失效和关闭策略复核。业务语义见 GOAL.md，迁移与预演见 UV-USAGE.md；test_reliability_regressions、test_gateway、test_delivery_migration 与 test_web_access 离线通过。未执行真实投递或回放。
分诊标签保留原实施类别，完成状态以 Resolution 和 TODO.md 为准；真实环境验收另列于 TODO.md。
