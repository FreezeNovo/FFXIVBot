# 移除群时保留投递历史

Status: ready-for-agent
Category: bug
Resolution: completed (2026-11-02; offline verified)

## 修复前事实

`ff14_core/storage.py::delete_onebot_group` 直接删除 target；schema 15 中
notification_candidate → target 和 delivery_attempt → notification_candidate
使用 ON DELETE CASCADE，导致群移除时历史一并删除。
`tests/test_calendar.py::test_removing_group_cascades_delivery_history`
验证了此行为。它解决了旧外键报错，但不符合 [ARCHITECTURE.md](../../../ARCHITECTURE.md)
的投递历史不可变要求。

## 验收条件

- 使用停用/归档或等价保留方案：移除服务资格后停止发送，同时仍能审计既有候选与 attempt。
- 重复移除与重新添加同一目标的身份、策略和历史行为明确且经过测试。
- 可重复迁移在保留旧历史的前提下执行，支持备份与恢复检查。
- WebUI 文案与操作结果一致；不得通过删除数据库或真实历史验证。

## Comments

2026-10-30 核查登记。现有级联删除回归测试证明当前事实，不代表它满足现行保留要求。


2026-11-02：已实现软归档、关闭策略、保留候选/attempt；重新添加沿用身份而不恢复订阅。schema 16 移除级联删除；test_calendar、test_reliability_regressions、test_delivery_migration 覆盖移除/重加/迁移/备份恢复。真实数据未改动。
分诊标签保留原实施类别，完成状态以 Resolution 和 TODO.md 为准；真实环境验收另列于 TODO.md。
