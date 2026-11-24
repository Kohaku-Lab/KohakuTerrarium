---
title: 瀏覽器
summary: 用內建的 browser 外掛讓生物操作真正的 Chromium。安裝、啟用、六個瀏覽器工具、網路政策、sandbox 與 permgate、密鑰、限制。
tags:
  - guides
  - browser
  - plugins
  - tools
---

# 瀏覽器

給想讓生物操作真正網頁瀏覽器的讀者：登入測試站台、填表單、點完一整段流程，
或是檢查它剛做好的網頁 UI。

內建的 `browser` 外掛會給生物一個長時間存活的 Chromium，以及六個用來操作它的
`browser_*` 工具。瀏覽器在多次呼叫之間會保留狀態（cookie、登入、開著的分頁）；
模型以文字加上元素 ref 的形式讀取頁面，再對這些 ref 下動作；頁面發出的每個請求
都會經過一個過濾代理（proxy），讓它碰不到你的私有網路。

它和 `web_fetch` 不同：`web_fetch` 只會算繪一個頁面、回傳文字，沒有工作階段，
也不能互動。只是要讀內容就用 `web_fetch`；生物需要在網站上「做事」時，才用
browser 外掛。

概念先讀：[外掛](../concepts/modules/plugin.md) 與
[工具](../concepts/modules/tool.md)。瀏覽器由外掛擁有，工具也由外掛提供；
框架核心本身沒有任何瀏覽器相關的東西。

## 安裝

瀏覽器是選用的 extra。沒裝它的框架照樣能正常啟動，只有瀏覽器工具會失敗，
並告訴你該裝什麼。

```bash
pip install "kohakuterrarium[browser-use]"
playwright install chromium
```

第一行安裝 Playwright，第二行下載 Playwright 要驅動的 Chromium。沒有下載的話，
外掛會改用已安裝的 Google Chrome，再來是 Microsoft Edge（見[啟動](#啟動)）。
`kohakuterrarium[full]` 已包含 `browser-use`；較早的 `browser` extra 是
`web_fetch` 背後的 Crawl4AI 算繪器，不會安裝 Playwright。

## 在生物上啟用

這個外掛登記在內建目錄裡，而且**預設關閉**：外掛沒打開之前，生物拿不到任何
瀏覽器工具。

### 在設定檔裡

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true      # 連到 localhost 上的開發伺服器
```

不寫 `options` 就全部套用預設值，瀏覽器只能連到公開網站。瀏覽器是在第一次
瀏覽器呼叫時才啟動，不是在生物啟動時。

### 執行期間

每個生物都會發現這個外掛，所以可以在執行中的工作階段把它打開：在聊天裡輸入
`/plugin enable browser`，或使用網頁 UI 的 Plugins 分頁（也能在那裡編輯它的
options）。修改過的 options 從下一次瀏覽器呼叫開始生效：目前的瀏覽器關閉，
再用新的 options 啟動一個。

停用外掛會立刻關閉瀏覽器，工具也會從生物身上消失；再次啟用後，下一次呼叫會啟動
一個全新的瀏覽器（分頁、cookie、登入都不會帶過來）。

### 從 Python

把設定項目寫進你用來建構的設定裡，或直接傳入外掛實例：

```python
from kohakuterrarium import Agent
from kohakuterrarium.builtins.plugins.browser.plugin import BrowserPlugin

agent = await Agent.build(
    "path/to/creature",
    plugins=[BrowserPlugin(options={"allow_loopback": True})],
)
```

帶有 `plugins=[{"name": "browser", "options": {...}}]` 的 `AgentConfig`
效果相同。

## 六個工具

| 工具 | 類型 | 用途 |
|---|---|---|
| `browser_navigate` | 對外連線 | 開啟一個 `url`（`action` 為 `goto`，即預設值），或做 `back` / `forward` / `reload`，這三種會忽略 `url`。回傳頁首資訊與帶 ref 的頁面文字（頁面超過 8000 個字元時只列可互動元素）。 |
| `browser_snapshot` | 讀取 | 以帶 ref 的無障礙樹讀取目前頁面。可用 `ref`（單一子樹）、`depth`（0 表示不限）、`query`（含某段文字的行與其祖先）或 `interactive`（只列控制項）縮小範圍；`boxes` 會加上每個元素在 viewport 中的像素位置。 |
| `browser_act` | 變更 | 在單一分頁上依序執行一批 `actions`。每個動作回傳一行狀態，加上頁面的變化。`files` 列出上傳動作要用的路徑。 |
| `browser_screenshot` | 讀取 | 把 viewport、單一元素（`ref`，優先於 `full_page`）或整頁（`full_page`）擷取成圖片；`path` 會另外存成檔案。 |
| `browser_tabs` | 工作階段 | `list`（預設）、`new`（可帶 `url`）、`select` 或 `close` 分頁，或 `close_browser`。 |
| `browser_eval` | 指令碼 | 在頁面裡執行 JavaScript 運算式或函式，可選擇作用於某個元素 `ref`，回傳 JSON 結果。 |

除了 `browser_tabs` 以外，每個工具都接受選用的 `tab`（分頁 id，例如 `t2`；
預設為使用中的分頁）。工具依風險拆開，讓依名稱判斷的閘門（`permgate`、
`sandbox`）分得出「讀頁面」、「改頁面」和「在頁面裡跑程式」的差別。

必須填滿每個參數的模型（嚴格模式的函式呼叫）可以對用不到的參數傳空值：`""`、
`0`、`false` 或 `[]` 都代表「沒有提供」。

### 一批動作

`browser_act` 接受一串動作物件，每個都有一個 `kind` 與下表的欄位。

| kind | 欄位 |
|---|---|
| `click` | `ref`，或 viewport 像素座標 `x` / `y`；`button`、`double`、`modifiers` |
| `type` | `ref`；`text`，或 `secret`（已設定的密鑰名稱）；`submit`、`slowly` |
| `select` | `ref`、`values`（選項標籤） |
| `check` | `ref`、`checked`（預設 true） |
| `press` | `key`，例如 `Enter` 或 `Control+A`；可選 `ref` |
| `hover` | `ref`，或 `x` / `y` |
| `scroll` | `ref`（捲動到可見範圍），或 `dx` / `dy` 像素 |
| `drag` | 起點 `ref` 或 `x` / `y`；終點 `to`（一個 ref）或 `to_x` / `to_y` |
| `upload` | `ref`、`files`（指向該次呼叫 `files` 清單的索引） |
| `wait` | `text`、`text_gone`、`seconds` 擇一 |
| `dialog` | `accept`（預設 true），prompt 對話框用 `text` |

動作用不到的欄位可以傳空值。兩個欄位都可能生效時：非空的 `ref` 優先於 `x` / `y`
（`0, 0` 表示沒有座標），`secret` 優先於 `text`（空的 `text` 會清空欄位），`wait`
依序採用 `text`、`text_gone`、大於 0 的 `seconds`。在屬於別種動作的欄位上給實際
的值會被拒絕。

在文字型的工具呼叫格式裡，所有參數都是字串，所以 `actions` 也可以是 JSON 字串
或呼叫的本文。

## 工具如何搭配使用

這些工具圍繞一個循環設計：**讀頁面，再用 ref 成批動作，然後讀結果。**

1. **開啟頁面。** `browser_navigate` 回傳一行頁首（`[t1]` 與 URL），以及頁面
   文字，也就是 `browser_snapshot` 給出的同一棵清理過的樹，每個控制項帶一個
   ref。頁面超過 8000 個字元時只列可互動元素，並附一句說明文字被省略。
2. **需要時再讀。** `browser_snapshot` 給出整頁內容：標題、文字、控制項。頁面
   很大時用 `query` 或 `interactive` 縮小範圍，不要整頁讀完。ref 的樣子是
   `e12`；分頁載入新頁面後會帶前綴，例如 `f1e12` 或 `f2e12`，frame 裡的元素
   帶該 frame 的前綴。ref 請照原樣使用。
3. **一次送出一批。** 把同一個意圖的步驟放進同一個 `browser_act`：輸入名稱、選方案、
   勾選方塊、按儲存，再 `wait` 確認訊息出現。動作依序執行，遇到第一個失敗就停，
   其餘的會標成未執行。之所以一個意圖一次呼叫，是因為同一回合裡的工具呼叫會並行
   執行，分開的呼叫無法表達「先點，再輸入，點失敗就停」；而且每次來回模型，正是
   瀏覽任務裡最慢的部分。
4. **讀結果。** 同一頁面上的一批動作，結果是變化：相對於這批動作前的快照，有哪些
   行被新增（`+`）或變更（`~`）、哪些元素換了位置（標成 `(moved)`）、URL 與標題的
   變化、新開的分頁、對話框、下載與被擋下的請求，或是 `No change in the page
   snapshot.`。變化會藏住的比顯示的多時（篩掉大部分的行、關掉遮罩、變化被截斷），
   結果改為顯示整個頁面。若這批動作換到新頁面或開了分頁，則像 `browser_navigate`
   一樣顯示那個頁面的文字。回報成功之前，先確認結果裡真的有你要的結果。
5. **長頁面與延遲載入。** 要到達很長或延遲載入頁面的底部，用較大的 `dy` 做
   `scroll`（或 `press` `End`），`wait` 新內容出現，重複到結果顯示 `No change
   in the page snapshot.` 為止。還沒有內容的頁面會說明它可能仍在載入；`wait`
   你期待的文字後再讀一次。
6. **退回用像素。** 沒有 ref 的東西（canvas、圖表、自製的拖曳區）可以用
   `browser_screenshot` 看，`click`、`hover`、`drag` 也接受 `x` / `y` 座標。
   截圖像素就是 device scale 為 1 的 CSS 像素，從圖上讀到的座標就是要點的座標。
   `browser_snapshot` 加上 `boxes` 會以文字給出同樣的座標。

Ref 只對模型最後看到的那一版頁面有效。每批動作之前，工作階段會重新取一次快照；
元素已經不見、或 ref 現在指向別的元素時，會立刻拒絕並指名是哪個 ref，而不是等到
逾時。這時請重新快照，改用新的 ref。

### 分頁、對話框、下載、上傳

- **分頁**有固定的 id（`t1`、`t2`…）。點擊開出的分頁會成為使用中分頁，動作結果
  會說明這件事，並顯示它的文字。Chromium 已經通知要開新視窗時，動作會最多
  等 3 秒讓它開好再回傳。
- **對話框。** `alert` 與 `beforeunload` 會自動接受並回報。`confirm` 或
  `prompt` 對話框會讓該次呼叫以「dialog pending」結束；在用 `dialog` 動作回答
  之前，其他瀏覽器呼叫都會回傳錯誤，說明有對話框在等待。
- **下載**存放在生物工作目錄下的 `browser-downloads/`，結果會給出存檔路徑。
  兩秒後還沒下載完的檔案會回報為進行中。每個瀏覽器最多保留 50 個下載、總共
  1 GB，之後的下載會被拒絕並回報。
- **上傳**的路徑來自該次呼叫的 `files` 參數（相對於工作目錄），因此 sandbox
  會像檢查其他檔案讀取一樣檢查它們。藏在自訂樣式按鈕後面的檔案輸入框沒有自己的
  ref；把 `upload` 對準看得到的那個按鈕，工作階段會改走檔案選擇視窗。

### 瀏覽器保留什麼、何時重來

瀏覽器在第一次瀏覽器呼叫時啟動（`browser_tabs` 的 `list` 不會啟動它）。閒置
超過 `idle_close_s`、當機，或呼叫 `close_browser` 時，瀏覽器會關閉；下一次呼叫
會啟動新的，並說明狀態已遺失。除非設定了 `profile_dir`，新瀏覽器用的是用完即丟
的設定檔，所以登入狀態撐不過重啟。

生物停止，或外掛被卸載、停用時，瀏覽器就永久關閉。之後的呼叫會直接失敗，不會在
背後偷偷再開一個。

生物的子代理共用它的瀏覽器。生物與子代理的呼叫會在這個瀏覽器上排隊，一次一個。

## Options

所有 option 與預設值。未知的 option 名稱會在建構生物時被拒絕。

| Option | 類型 | 預設 | 用途 |
|---|---|---|---|
| `headless` | bool | `true` | 不開視窗執行。設成 `false` 可以看著瀏覽器操作。 |
| `channel` | string | 無 | 使用已安裝的瀏覽器 channel，例如 `chrome` 或 `msedge`。 |
| `executable_path` | string | 無 | 要執行的 Chromium 系瀏覽器路徑。 |
| `viewport` | `"WIDTHxHEIGHT"` 或 `[w, h]` | `1280x800` | 頁面尺寸（CSS 像素）；寬 320–7680，高 200–4320。 |
| `profile_dir` | string | 無 | 持久化的設定檔目錄。不設定表示每次啟動都用全新、用完即丟的設定檔。 |
| `allow_hosts` | list | `[]` | 非空時，頁面只能連到這些主機。 |
| `block_hosts` | list | `[]` | 頁面永遠不能連到的主機。比 `allow_hosts` 先檢查。 |
| `allow_loopback` | bool | `false` | 允許 `localhost` 與其他 loopback 位址（本機開發伺服器）。 |
| `allow_private` | bool | `false` | 允許私有網路位址（10/8、172.16/12、192.168/16、電信級 NAT、IPv6 unique-local）。 |
| `secrets` | map | `{}` | 密鑰名稱對應到 `env:VAR_NAME` 參照，供 `type` 動作使用。見[密鑰](#密鑰)。 |
| `nav_timeout_s` | number | `30` | 導覽逾時秒數（大於 0，最多 3600）。 |
| `action_timeout_s` | number | `5` | 每個動作與頁面讀取的逾時秒數（大於 0，最多 3600）。 |
| `snapshot_max_chars` | int | `20000` | 快照長度上限（1000–1000000）。被截斷的快照最後一行會說明省略了多少、該怎麼縮小範圍。 |
| `idle_close_s` | number | `600` | 超過這麼多秒沒有呼叫就關閉瀏覽器（大於 0，最多 86400）。 |

### 啟動

設了 `executable_path` 時只會試那一個瀏覽器；設了 `channel` 時只試那個 channel。
兩者都沒設時，外掛依序嘗試 Playwright 內附的 Chromium、已安裝的 Google Chrome、
Microsoft Edge。頁面一律使用固定的 viewport，device scale factor 為 1。

## 網路政策

所有瀏覽器流量都經過工作階段自己擁有、跑在 127.0.0.1 上的過濾代理。Chromium 被
設定成連 loopback 流量也要走代理，QUIC 會關閉，WebRTC 也不能在代理之外送 UDP。
代理看得到每一個請求：重新導向的每一跳、子資源、彈出視窗、service worker 的請求
與 WebSocket 交握，所以頁面無法靠重新導向或在背景載入來碰到被封鎖的位址。

對每一條連線，代理會：

1. 只接受 `http` 與 `https`；
2. 拒絕 `block_hosts` 裡的主機，接著在 `allow_hosts` 非空時，拒絕不在其中的主機；
3. sandbox 外掛啟用時詢問它（見下文）；
4. 解析主機名稱並拒絕特殊位址：loopback 除非設了 `allow_loopback`；私有、電信級
   NAT 與 IPv6 unique-local 除非設了 `allow_private`；而 link-local（包含雲端
   metadata 位址 `169.254.169.254`）、unspecified、multicast 與保留區段一律拒絕。
   包在 IPv6 位址裡的 IPv4 位址，以那個 IPv4 位址判斷；
5. 連到它檢查過的那個位址，所以第二次 DNS 回應無法指向別處。

被擋下的請求會收到代理回的 403，下一個工具結果會在 `Blocked N requests` 底下列出
它和原因。主頁面（或重新導向鏈上任何一個主機）被擋下的導覽會變成工具錯誤，並指出
URL 與原因。`browser_act` 的某個動作導向被擋下的頁面（連結或表單重新導向到被擋下
的主機）時，該動作會回報為失敗並指出主機與原因，結果會寫 `Navigation blocked`
以及頁面沒有載入。`browser_navigate` 也會一開始就拒絕非網頁的 scheme（`file:`、
`javascript:`…）以及內嵌帳密的 URL。

**本機開發伺服器。** loopback 預設是封鎖的。要測試本機應用程式，只差一個
option：`allow_loopback: true`。若同時要把外部網際網路擋在外面，再加上
`allow_hosts`：

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["localhost", "127.0.0.1"]
```

**主機樣式。** `allow_hosts` 與 `block_hosts` 的項目可以是：

| 樣式 | 符合 |
|---|---|
| `example.com` | 剛好是 `example.com`，任何連接埠 |
| `*.example.com` | `example.com` 的任何子網域（不含 `example.com` 本身） |
| `example.com:8443` | 只有 `example.com` 的 8443 連接埠 |
| `127.0.0.1:5173` | 只有該位址的該連接埠 |
| `[::1]:5173` | 帶連接埠的 IPv6 位址 |

名稱比對前會轉成小寫、做 IDNA 編碼，並去掉結尾的點。對 IP 位址使用萬用字元會被
拒絕。寫在 `block_hosts` 裡的 IP 位址，也會封鎖解析到該位址的主機名稱。

link-local 位址就算列在 `allow_hosts` 裡也會被拒絕；沒有任何 option 能放行它們。

## 搭配 sandbox

兩者都啟用時，瀏覽器會遵循 [`sandbox`](../reference/builtin-plugins.md#sandbox)
外掛。代理對每一條連線都會詢問 sandbox，所以 sandbox 的 options 一改，從下一個
請求起就生效。sandbox 被開啟或關閉，或它拒絕所有網路時，瀏覽器已開的連線會被切斷。

- **`network: deny`**：任何瀏覽器工具都不會執行。sandbox 會在呼叫到達瀏覽器之前
  就拒絕。
- **`network_allowlist`**：`browser_navigate` 與 `browser_tabs` 的 URL 參數會拿來
  比對，代理開出的每條連線（含子資源）也一樣。這份清單只寫主機名稱（沒有連接埠），
  並疊加在瀏覽器自己的 `allow_hosts` 之上；主機必須兩邊都通過。
- **`os_enforce: require`**：瀏覽器會拒絕啟動（已在跑的也會關閉），因為 Chromium
  無法在 sandbox 的子行程隔離下執行。瀏覽器被當成網路工具看待，所以 `syscall`
  等級不會擋它。
- **檔案路徑**：`browser_act` 的 `files`（讀取）與 `browser_screenshot` 的
  `path`（寫入）會經過和其他檔案工具相同的路徑檢查。
- **`backend: audit`** 會記錄它原本要拒絕的東西，然後放行。

## 搭配 permgate

[`permgate`](../reference/builtin-plugins.md#permgate) 不需要任何瀏覽器專屬設定。
把會離開頁面、改變頁面或在頁面裡跑程式的工具加上閘門，唯讀的工具則放行：

```yaml
plugins:
  - name: browser
  - name: permgate
    options:
      gated_tools:
        - browser_navigate
        - browser_act
        - browser_eval
        - browser_tabs
```

`browser_snapshot` 與 `browser_screenshot` 只讀取頁面，不需要閘門。
`gated_tools` 留空代表每個工具都要批准，所以請明確列出來。

## 密鑰

options 裡放的是參照，永遠不是值本身：

```yaml
plugins:
  - name: browser
    options:
      secrets:
        staging_pw: "env:STAGING_PASSWORD"
```

`type` 動作若指定 `secret: staging_pw`（取代 `text`），就會輸入
`STAGING_PASSWORD` 的值，這個值在動作執行時才從環境變數讀取，長度至少要四個字元。
在每個文字結果裡，它（包括原始值以及 URL、JSON 編碼後的寫法）都會顯示成
`[secret:staging_pw]`。密碼欄位也會遮蔽：在快照裡，密碼欄位的值顯示成
`(redacted)`（若是這個密鑰填入的，則顯示 `[secret:staging_pw]`）；同一個值出現在
結果其他地方（標題、URL）時顯示成 `[redacted]`。

仍然看得到密鑰的地方：

- **截圖。** 頁面若把這個值當成可見文字顯示出來，圖片裡就看得到。
- **`browser_eval`。** 指令碼以頁面的權限執行，讀得到欄位的值。牽涉到密鑰時，
  請用 `permgate` 替 `browser_eval` 加上閘門。
- **網站本身**，這是當然的。只在你信任的網站上輸入密鑰。

## 不受信任的頁面內容

所有由頁面決定的內容（文字、標題、元素名稱、對話框訊息、檔名、指令碼結果、頁面
給的錯誤訊息）都包在一個 `page_content` 元素裡，上面帶著頁面的來源（origin）、
這個工作階段固定的隨機 id，以及 `trust="untrusted"`。內容裡任何看起來像結束標籤
的東西都會被跳脫，所以頁面無法提早關掉這個包裝。外掛會在系統提示加上一小段
`## Browser pages`，說明這些內容是資料，永遠不是指令。

快照也會丟掉人看不到的文字：標成 `aria-hidden` 的元素，以及透明、極小、與背景
同色、遠在畫面外或被裁掉的非互動文字。結果會說明丟掉了幾個區塊。可互動元素一律
保留，因為真實控制項上給螢幕閱讀器用的標籤是正當的。`browser_eval` 讀的是原始
頁面，所以指令碼仍看得到隱藏文字；它的輸出也以同樣方式包裝。

這是**縱深防禦，不是保證**。把內容標成不受信任能降低提示注入的成功率，但擋不住
它，頁面仍然可能說服模型。請限制瀏覽用生物能碰到、能做的事：`allow_hosts` 收緊、
用 `permgate` 替會變更的工具加閘門，也不要給它用不到的密鑰或工具。

## 多使用者主機

在有使用者帳號的伺服器上，只有管理員的 engine 可以使用主機資源。對其他使用者
（以及匿名名額）來說，瀏覽器只能以 headless 執行，搭配用完即丟的設定檔、內附的
Chromium，且只能連公開位址。若生物的 options 要求更多（`headless: false`、
`profile_dir`、`executable_path`、`channel`、`allow_loopback` 或
`allow_private`），每次瀏覽器呼叫都會得到一個列出被拒 option 的錯誤。單人使用的
主機、`kt run` 與 CLI 不受此限制。

## 限制與已知缺口

- 每個 `browser_act` 最多 20 個動作；`wait` 最多 10 秒。
- 最多 20 個開著的分頁；頁面超出上限開的分頁會被關閉並回報。
- `browser_eval` 的結果在 10000 個字元處截斷，指令碼必須在 `action_timeout_s`
  內完成。
- 截圖為 PNG；PNG 會超過約 500 KB 時改用 JPEG。整頁截圖最高 8000 像素。
- 每個動作之後，工作階段會等頁面安靜下來（150 ms 內沒有 DOM 變化，最多 2 秒）。
  它看不到尚未觸發的計時器：頁面稍後才更新時，請加一個 `wait` 等你預期的文字。
- **只有原生工具呼叫時，截圖才會送到模型。** 在文字型的工具呼叫格式（`bracket`、
  `xml`）下，工具結果是以文字送回去的，模型只拿得到截圖的文字那一行（擷取了什麼、
  存在哪裡），看不到圖片。截圖重要時請用 `tool_format: native`，或用 `path` 把截圖
  存下來。
- 每個生物一個瀏覽器；它的呼叫一次只跑一個。
- 只支援 Chromium，透過 Playwright。
- 不支援：附加到你自己正在執行的 Chrome 或它的預設設定檔；隱匿、規避反機器人
  或解 CAPTCHA（遇到 CAPTCHA 或兩步驟驗證時應交給使用者）；操作桌面或其他應用程式。
- 尚未實作：專門用來瀏覽的子代理、提示注入分類器、供應商原生的 computer-use 工具，
  以及記住過往瀏覽流程的記憶。

## 範例

[`examples/agent-apps/web_tester`](../../../examples/agent-apps/web_tester/)
是一個檢查本機網頁應用程式的小型生物。它啟用瀏覽器並設定 `allow_loopback`，
`allow_hosts` 只限 `localhost` 與 `127.0.0.1`；使用原生工具呼叫讓截圖能送到模型；
提示詞要求它只回報頁面上真的出現的結果。

```bash
kt run examples/agent-apps/web_tester --llm <your preset>
```

## 疑難排解

- **「the browser tools need Playwright, which is not installed」。** 安裝 extra：
  `pip install "kohakuterrarium[browser-use]"`，再執行 `playwright install chromium`。
- **「no usable Chromium was found」。** 執行 `playwright install chromium`，或把
  `executable_path` 或 `channel` 指向已安裝的 Chromium 系瀏覽器。
- **「the browser needs the Proactor event loop on Windows」。** Playwright 會以
  子行程啟動它的 driver，而只有 Proactor loop 支援子行程。程式裡有東西改用了
  selector loop：最常見的是用 `--reload` 啟動的伺服器（在 Windows 上，uvicorn 會用
  selector loop 跑重新載入的應用程式），或設定了 `WindowsSelectorEventLoopPolicy`
  的程式碼。請不加 `--reload` 啟動伺服器，或移除那個 policy。
- **導覽被擋下。** 錯誤訊息會指出 URL 與原因。本機伺服器請設
  `allow_loopback: true`（區網內的機器還要 `allow_private: true`）；主機不在
  `allow_hosts` 裡就把它加進去；也檢查一下 sandbox 的 `network_allowlist`。
  link-local 位址無法放行。
- **每次呼叫都是「dialog pending」。** 有一個 `confirm` 或 `prompt` 對話框開著。
  在 `browser_act` 裡用 `dialog` 動作回答它（`accept` 設 true 或 false，prompt
  用 `text`）。
- **「ref eN is no longer on the page」/「now points at a different element」。**
  頁面在模型上次看到之後變了。重新快照，改用新的 ref。
- **「the browser tools need the browser plugin」。** 某個 `browser_*` 工具被列在
  `tools:` 底下，但沒有啟用外掛。改成啟用 `browser` 外掛，它會帶來全部六個工具。
- **「the browser session is closed」。** 生物已停止，或外掛被停用。重新啟用外掛，
  下一次呼叫就會啟動新的瀏覽器。

## 延伸閱讀

- [參考 / 內建外掛](../reference/builtin-plugins.md#browser)：option 一覽表。
- [參考 / 內建項目](../reference/builtins.md#browser)：六個工具。
- [外掛](plugins.md)：外掛如何掛進 agent，包含 `sandbox` 與 `permgate`。
- [設定](configuration.md)：`plugins:` 在生物設定裡的位置。
