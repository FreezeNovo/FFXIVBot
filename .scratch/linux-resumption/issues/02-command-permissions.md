# 群命令权限与功能策略检查

Status: ready-for-agent
Category: bug
Resolution: completed (2026-11-02; offline verified)

## 修复前事实

`ff14_core/application.py` 的 `CommandService.handle` 在确认群已启用后，
直接响应状态、活动和维护命令；仅数据源诊断分支检查 owner。
`tests/test_application.py::test_status_command_is_framework_independent`
明确使用普通 member 并断言成功，和 [ARCHITECTURE.md](../../../ARCHITECTURE.md) 的
状态命令仅限所有者要求冲突。

## 验收条件

- 所有者诊断权限由 Core 统一判断，涵盖状态及其别名。
- 群成员手动查询按 [GOAL.md](../../../GOAL.md) 的群级功能策略授权；独立表达手动查询权限与主动订阅，不以群白名单代替业务授权。
- 覆盖所有者、已启用群普通成员、停用/未知群、私聊和策略热更新。
- 将现有接受错误行为的测试改成先失败的回归测试，并补网关事件边界验证。

## Comments

2026-10-30 核查登记；未改变真实群权限或用户策略。新增字段的默认值若无既定产品决定，应先澄清，不能静默固化。


2026-11-02：诊断含别名仅允许数据库所有者；活动/维护群员查询分别显式授权，默认关闭且可在 WebUI 修改，所有者在已启用群可预览。test_application、test_reliability_regressions、test_gateway、test_web_access 通过；包括真实事件类的离线映射验证。未变更实际群策略。
分诊标签保留原实施类别，完成状态以 Resolution 和 TODO.md 为准；真实环境验收另列于 TODO.md。
