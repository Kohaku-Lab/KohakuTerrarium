---
title: 前端版面
summary: Vue 3 dashboard 怎麼編排、在哪裡擴充、事件怎麼從後端流到 UI。
tags:
  - guides
  - frontend
  - ui
---

# 前端版面

給使用或自訂 `kt web` / `kt app` / `kt serve` 提供的 web dashboard 的讀者。

Dashboard 用的是可設定的二元 split tree：每個區塊不是 leaf (一個 panel) 就是 split (兩個子節點加一個可拖的分隔線)。Preset 可以一次換掉整棵樹；edit 模式可以就地調整。

參考：[Serving](serving.md)，怎麼把 dashboard 打開。

## 核心觀念

- **Panel**：單一職責的 view (Chat、Files、Activity、State、Canvas、Debug、Settings、Terminal 等等)。Panel 在 `stores/layoutPanels.js` 註冊，以 id 查詢。
- **Split tree**：二元樹，每個節點不是 *leaf* (渲染一個 panel) 就是 *split* (把空間切成兩半，中間有個可拖的分隔線)。Split 可以是水平 (左 | 右) 或垂直 (上 / 下)。
- **Preset**：一棵具名的 split tree 設定。切 preset 會直接換掉整棵樹。Preset 分兩種：內建 (KT 附的) 與 user 自訂。
- **Header**：頂列，放 instance 資訊、preset 下拉、編輯版面按鈕、Ctrl+K 開 palette、stop 按鈕。
- **Status bar**：底列，放 model 快速切換、session id、job 數、執行時間。

## 預設的 preset

| 快捷鍵 | Preset | 版面 |
|----------|--------|--------|
| Ctrl+1 | Chat Focus | chat \| status (上) + creature state (下) |
| Ctrl+2 | Workspace | files \| editor+terminal \| chat+activity |
| Ctrl+3 | Multi-creature | creatures \| chat \| activity+state |
| Ctrl+4 | Canvas | chat \| canvas+activity |
| Ctrl+5 | Debug | chat+state (上) / debug (下) |
| Ctrl+6 | Settings | settings (全螢幕) |

每個新開啟的 instance 都落在 Chat Focus，不論它是單一生物還是多生物的圖：Status 側欄自帶生物列表，多生物圖不用切換版面就能操作。側欄上的 Creatures 圖示顯示成員數，並會在你看別處時隨圖成長而發光。Multi-creature 仍留在 Ctrl+3 作為寬版版面。每個 instance 上次用的 preset 存在 localStorage，並且永遠優先於預設值。

面板選擇器和命令面板會為每個面板顯示一行說明；兩個舊別名 (`file-tree`、`editor-status`) 仍能被舊 preset 解析，但不再提供給使用者選擇。

## Edit 模式

按 **Ctrl+Shift+L** 或點 header 的編輯按鈕進 edit 模式。每個 panel leaf 會出現一條琥珀色的 bar：

- **Replace**：透過 picker modal 把 panel 換成任何已註冊的 panel
- **Split H / Split V**：把當前 leaf 切兩半，產生一個新的空 slot
- **Close**：移除這個 panel (兄弟節點會接手父節點的空間)
- **"+ Add panel"** 按鈕，出現在空 slot 上

頂部的 edit 模式 banner 提供：
- **Save**：存回去 (只限 user preset；內建 preset 不能覆寫)
- **Save as new**：用自訂名字另存新的 user preset
- **Revert**：丟掉所有變更，還原原本
- **Exit**：離開 edit 模式 (如果有未存的變更會問一下)

所有編輯都跑在 preset 的深 clone 上。除非你明確存檔，原本永遠不會被動到。

## 鍵盤快捷鍵

| 快捷鍵 | 動作 |
|----------|--------|
| Ctrl+1..6 | 切換到某個 preset |
| Ctrl+Shift+L | 切換 edit 模式 |
| Ctrl+K | 開 command palette |
| Esc | 離開 edit 模式 |

Ctrl+K 就算 input 聚焦也會觸發。Preset 快捷鍵在 text input/textarea 裡會被擋掉。

## Command palette

按 Ctrl+K 打開。對所有已註冊指令做模糊比對：

- `Mode: <preset>`：切換到任一 preset
- `Panel: <panel>`：把 panel 加到它偏好的區域
- `Layout: edit / save as / reset`
- `Debug: open logs`

前綴路由：`>` 指令 (預設)、`@` mention、`#` 工作階段、`/` slash 指令。

## Panel 介紹

### Chat
主要對話介面。支援訊息編輯+重跑、重新生成、工具呼叫折疊、子代理巢狀顯示。

### Status (分頁側欄)
五個分頁：Session (代理、模型、provider、工作階段 id、狀態)、Creatures (圖裡每個成員：點擊聚焦、啟動/停止、按生物切換模型，以及頻道；側欄圖示帶成員數)、Tokens (in/out/cache + context bar 與壓縮門檻)、Jobs (執行中的工具呼叫與 stop 按鈕)、Modules (完整的模組介面)。

### Overview
同樣的工作階段身份、token 用量與執行中作業，但是一頁捲動、沒有分頁。不在任何預設 preset 裡；想一次看完全部時從面板選擇器裡選它。

### Jobs
只有模型、context bar 與執行中的作業。Settings preset 使用它。

### Creature State (分頁)
四個分頁：Drives (聚焦生物的驅動，管理能力與完整 Drives 面板相同：暫停、恢復、喚醒、取消、完成、進度紀錄，以及新增目標表單；範圍切換可看整個圖，「開啟完整面板」會把 Drives 面板以抽屜開啟)、Scratchpad (代理工作記憶的 key-value)、Memory (對工作階段事件做 FTS5 搜尋)、Compaction (歷次壓縮紀錄)。

### Drives
工作階段裡的全部驅動，按受派者分組，含完整紀錄詳情。可從面板選擇器、標題列徽章，以及 Creature State 面板的「開啟完整面板」進入。

### Files
檔案樹加 refresh，再加一個 "Touched" view：按動作分組顯示代理讀過/寫過/錯過的檔案。

### Editor
Monaco editor，有檔案 tab、髒狀態指示、Ctrl+S 存檔。Markdown 檔 (.md/.markdown/.mdx) 可以切換 Monaco (程式碼模式) 與 Vditor (有工具列、數學、程式碼區塊的 WYSIWYG markdown)。

### Canvas
自動偵測助理訊息裡的長 code block (15+ 行) 與 `##canvas##` 標記。顯示語法 highlight 的程式碼 (附行號)、渲染好的 markdown、或 sandboxed HTML。Tab 上有複製與下載按鈕。

### Terminal
xterm.js terminal，連到代理工作目錄下的 PTY shell (bash/PowerShell)。支援 Nerd Font 字符、resize、明暗主題。

### Debug (分頁)
四個分頁：Logs (透過 WebSocket 即時 tail API server log)、Trace (工具呼叫時序瀑布圖)、Prompt (目前 system prompt 加 diff)、Events (chat store 所有訊息)。

### Settings (分頁)
七個分頁：Session、Tokens、Jobs、Extensions (已安裝套件)、Triggers (當前觸發器)、Cost (token 成本估算)、Environment (cwd + 打馬賽克的環境變數)。

### Creatures (只有生態瓶才有)
生物列表加狀態 dot、加頻道列表。點一隻生物就切到它的 chat tab。

## 彈出成獨立視窗

在 edit 模式下，`supportsDetach: true` 的 panel 可以透過 Pop Out kebab 動作彈出去。彈出的視窗是個最小殼 `/detached/<instanceId>--<panelId>`，獨立連到後端。

## Status bar

永遠在底部：
- Instance 名稱加狀態 dot
- Model 快速切換 (下拉) 加設定齒輪
- Session id (點一下複製)
- 執行中的 job 數
- 已跑時間

## 技術細節

Split tree 存成純 JSON：
```json
{
  "type": "split",
  "direction": "horizontal",
  "ratio": 70,
  "children": [
    { "type": "leaf", "panelId": "chat" },
    { "type": "split", "direction": "vertical", "ratio": 50,
      "children": [
        { "type": "leaf", "panelId": "activity" },
        { "type": "leaf", "panelId": "state" }
      ]
    }
  ]
}
```

`LayoutNode.vue` 是遞迴元件：split 會渲兩個子節點加一個可拖的分隔線，leaf 用 `<component :is>` 渲 panel 元件。Panel 的執行期 props 透過 Vue 的 provide/inject 從 route 頁流下來。

## 延伸閱讀

- [Serving](serving.md)：用 `kt web` / `kt app` / `kt serve` 打開 dashboard。
- [Development / Frontend](../dev/frontend.md)：給貢獻者的架構文件。
