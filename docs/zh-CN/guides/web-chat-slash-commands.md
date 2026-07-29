# Web Chat 斜杠命令与技能

在空的 Web Chat 输入框开头输入 `/`，即可打开当前 Creature 的实时命令/技能菜单。继续输入可按命令名、别名或技能名过滤；使用上下方向键移动，Enter 或 Tab 选择，Escape 关闭，也可以点击条目。

命令始终排在技能之前。命令名或别名与技能名冲突时，命令优先。已禁用技能不会显示，服务端也会拒绝执行。`invocation_blocked` 仅禁止模型自动调用，不会阻止用户显式选择技能。

选择命令会调用命令接口并显示结构化结果。选择技能会以 `web:skill` 来源注入正常 Creature turn 队列，因此回复会像普通聊天一样流式显示并写入会话记录。

接口：

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/skill-input`

截图请按英文文档所列步骤操作；不要提交包含私人账号、令牌、路径或聊天内容的本地截图文件。
