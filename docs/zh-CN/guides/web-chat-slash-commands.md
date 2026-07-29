# Web Chat 目标与技能发现

在空的 Web Chat 输入框开头输入 `/`，即可打开当前 Creature 的实时目标/技能菜单。继续输入可按名称过滤；使用上下方向键移动，Enter 或 Tab 选择，Escape 关闭，也可以点击条目。

菜单只显示当前可用的 `/goal` 和已启用、可由 Creature 调用的技能。其他命令不显示，因为 Web UI 已经提供对应的按钮、选择器、状态面板或设置入口。例如，请使用模型选择器而不是 `/model`，使用状态面板而不是 `/status`，使用输入框旁的按钮而不是 `/compact`。

即使其他命令没有显示，完整的命令名和别名仍会屏蔽同名技能，避免候选项在服务端解析成不同对象。

选择 `/goal` 会调用命令接口，并在聊天中显示结构化结果。选择技能会插入 `/<skill>` 并作为普通聊天输入发送，由 Creature 自行决定如何使用该技能。除 `/goal` 外的斜杠开头文本也会作为普通聊天输入发送，不会绕过现有 Web UI。

接口：

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/command`

截图请按英文文档所列步骤操作；不要提交包含私人账号、令牌、路径或聊天内容的本地截图文件。
