# Koishi 相关调查归档

状态：归档。本文只记录既有 Koishi 部署和旧 `koishi-plugin-ff14calendar` 的历史边界，不属于 FFXIVBot 当前运行架构、迁移输入或兼容目标。

## 运维边界

- 既有 Koishi 继续由其原维护者管理；FFXIVBot 不安装、修改、停止或替换它。
- NapCat 是外部 OneBot V11 provider。FFXIVBot 只通过自己的 NoneBot2 网关连接，不写入 NapCat 配置。
- 同一 QQ 群只能由一套业务网关处理事件，避免广播造成双回复和重复投递。

## 旧插件观察

归档插件曾提供紧凑的“正在进行/未来活动”摘要、手动查询和多目标推送。这些只是需求层观察；其 ICS URL、目标 ID、Cron、模板、命令、代理配置、Node 依赖和无状态定时管道均不迁移，也不保证旧命令兼容。

FFXIVBot 当前的时间、来源快照、分类、通知候选和投递账本规则以 [`GOAL.md`](GOAL.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md) 和 [`AGENTS.md`](AGENTS.md) 为准。
