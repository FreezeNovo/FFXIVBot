# Issue tracker: Local Markdown

本仓库的任务和规格保存在 `.scratch/`，路径均相对于仓库根目录。

## 文件约定

- 每项功能一个目录：`.scratch/<feature-slug>/`。
- 规格：`.scratch/<feature-slug>/spec.md`。
- 实施任务：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，
  从 `01` 编号，每项任务独立成文件。
- 分诊状态在文件顶部使用 `Status:`，取值见
  [标签映射](triage-labels.md)。
- 分诊类别使用 `Category: bug` 或 `Category: enhancement`。
- 评论与讨论追加到文件底部的 `## Comments` 下。

技能要求“发布到任务系统”时，按上述规则创建本地文件。
要求“读取任务”时，读取指定路径；只有编号且存在多个匹配时，
先明确所属功能目录。

## 与项目权威文档的关系

任务文件保存具体问题、验收条件和讨论。
整体实现状态与下一步仍维护在 [TODO.md](../../TODO.md)，
通过链接关联具体任务。

产品与架构结论按 [AGENTS.md](../../AGENTS.md) 的权威信息规则
同步到对应文档；任务规格不能自行改变产品范围。

## Wayfinder 探索任务

使用 wayfinder 时：

- 探索地图：`.scratch/<effort>/map.md`，包含 Notes、
  Decisions-so-far 和 Fog。
- 子任务：`.scratch/<effort>/issues/<NN>-<slug>.md`。
- `Type:` 使用 `research`、`prototype`、`grilling` 或 `task`。
- 此类任务的 `Status:` 使用 `open`、`claimed`、`resolved`，
  表示探索执行状态；普通实施任务使用上述分诊状态。
- 依赖用 `Blocked by: NN, NN` 表示；所有依赖均为
  `resolved` 后解除阻塞。
- 从未阻塞、未领取的 open 任务中按编号选择；开始前保存
  `Status: claimed`。
- 完成后在 `## Answer` 下追加结论，改为 `resolved`，
  并向地图的 Decisions-so-far 添加摘要和相对链接。
