# Triage Labels

本仓库保留五个默认分诊状态。
普通任务在顶部的 `Status:` 行记录其中一个状态。

| 技能角色 | 本地状态值 | 含义 |
| --- | --- | --- |
| needs-triage | needs-triage | 等待维护者评估 |
| needs-info | needs-info | 等待补充信息 |
| ready-for-agent | ready-for-agent | 规格充分，可交由代理实施 |
| ready-for-human | ready-for-human | 需要人工实施 |
| wontfix | wontfix | 不予实施 |

技能要求应用某个分诊角色时，使用表中对应的本地状态值。
状态不代替项目验收要求；完成任务仍遵守
[AGENTS.md](../../AGENTS.md) 的完成定义。
