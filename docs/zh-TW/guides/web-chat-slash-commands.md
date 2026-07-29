# Web Chat 斜線命令與技能

在空的 Web Chat 輸入框開頭輸入 `/`，即可開啟目前 Creature 的即時命令／技能選單。繼續輸入可依命令名稱、別名或技能名稱篩選；使用上下方向鍵移動，Enter 或 Tab 選取，Escape 關閉，也可以點擊項目。

命令一律排在技能之前。命令名稱或別名與技能名稱衝突時，命令優先。已停用技能不會顯示，伺服器也會拒絕執行。`invocation_blocked` 只禁止模型自動呼叫，不會阻止使用者明確選取技能。

選取命令會呼叫命令 API 並顯示結構化結果。選取技能會以 `web:skill` 來源注入正常 Creature turn 佇列，因此回覆會像一般聊天一樣串流顯示並寫入工作階段紀錄。

API：

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/skill-input`

截圖請依英文文件列出的步驟操作；不要提交含有私人帳號、權杖、路徑或聊天內容的本機截圖檔。
