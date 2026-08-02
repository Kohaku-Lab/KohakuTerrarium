# Web Chat 目標與技能探索

在空的 Web Chat 輸入框開頭輸入 `/`，即可開啟目前 Creature 的即時目標／技能選單。繼續輸入可依名稱篩選；使用上下方向鍵移動，Enter 或 Tab 選取，Escape 關閉，也可以點擊項目。

選單只顯示目前可用的 `/goal` 與已啟用、可由 Creature 呼叫的技能。其他命令不顯示，因為 Web UI 已提供對應的按鈕、選擇器、狀態面板或設定入口。例如，請使用模型選擇器而不是 `/model`，使用狀態面板而不是 `/status`，使用輸入框旁的按鈕而不是 `/compact`。

即使其他命令沒有顯示，完整的命令名稱與別名仍會遮蔽同名技能，避免候選項在伺服器解析成不同物件。

選取 `/goal` 會呼叫命令 API，並在聊天中顯示結構化結果。選取技能會插入 `/<skill>` 並當作一般聊天輸入送出，由 Creature 自行決定如何使用該技能。除 `/goal` 外，以斜線開頭的文字也會當作一般聊天輸入，不會繞過既有 Web UI。

API：

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/command`

截圖請依英文文件列出的步驟操作；不要提交含有私人帳號、權杖、路徑或聊天內容的本機截圖檔。
