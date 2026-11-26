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
外掛會改用已安裝的 Google Chrome，再來是 Microsoft Edge（見[啟動](#啟動)）；
沒有主機資源的生物例外，它只會執行內附的 Chromium。
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
等舊的關閉完成後，再用新的 options 啟動一個（同一個設定檔上絕不會同時跑兩個
瀏覽器，見下文「瀏覽器保留什麼、何時重來」）。外掛停用期間修改的 options 會被保留，
等再次啟用後才生效；在這之間不會啟動任何瀏覽器。

停用外掛會立刻開始關閉瀏覽器，工具也會從生物身上消失；再次啟用後，下一次呼叫會在
舊瀏覽器關閉之後啟動一個全新的瀏覽器（分頁、cookie、登入都不會帶過來）。

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
| `browser_screenshot` | 讀取、寫檔 | 把 viewport、單一元素（`ref`，優先於 `full_page`）或整頁（`full_page`）擷取成圖片；`path` 會另外存成檔案。 |
| `browser_tabs` | 工作階段、對外連線 | `list`（預設）、`new`（可帶 `url`，會開啟該頁面）、`select` 或 `close` 分頁，或 `close_browser`。 |
| `browser_eval` | 指令碼 | 在頁面裡執行 JavaScript 運算式或函式，可選擇作用於某個元素 `ref`，回傳 JSON 結果。 |

除了 `browser_tabs` 以外，每個工具都接受選用的 `tab`（分頁 id，例如 `t2`；
預設為使用中的分頁）。工具依風險拆開，讓依名稱判斷的閘門（`permgate`、
`sandbox`）分得出「讀頁面」、「改頁面」和「在頁面裡跑程式」的差別。各工具在主要
用途之外還能做的事：

- 沒有瀏覽器在跑時，`browser_snapshot` 與 `browser_screenshot` 會啟動它（只有
  `list` 的 `browser_tabs` 從不啟動）。
- 帶 `path` 的 `browser_screenshot` 會寫入一個圖片檔；sandbox 把這個路徑當成
  寫入來檢查。
- 帶 `url` 的 `browser_tabs` `new` 會開啟該頁面，和 `browser_navigate` 一樣。
- `browser_eval` 以頁面的權限執行任意指令碼：它讀得到頁面持有的一切、能改變
  頁面，也能發出請求（經過和頁面相同的過濾代理）。它不是唯讀工具。

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
   帶該 frame 的前綴。ref 請照原樣使用。ref 只出現在用得上的地方：可互動元素
   （包括可點擊的，或帶 checked、expanded、pressed、selected 狀態的元素）、
   frame、對話框、表格、列、article，以及有名稱的容器；沒有名稱的地標與包裝層
   （例如 `main`、`list`）沒有 ref，從沒顯示過的 ref 會被拒絕。frame 顯示的
   來源與頁面不同時，會寫在它那一行，例如
   `- iframe [ref=e12] [origin=https://pay.example]:`。
3. **一次送出一批。** 把同一個意圖的步驟放進同一個 `browser_act`：輸入名稱、選方案、
   勾選方塊、按儲存，再 `wait` 確認訊息出現。動作依序執行，遇到第一個失敗就停，
   其餘的會標成未執行。導覽被網路政策擋下的動作也算失敗；這批動作到達時間上限時
   正在執行的動作同樣算失敗，上限是 60 秒，若（`action_timeout_s` + 10 秒）的
   三倍更長則取後者。之所以一個意圖一次呼叫，是因為同一回合裡的工具呼叫會並行
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
逾時。frame 裡的 ref 在該 frame 載入別的頁面後也會被拒絕，即使新頁面有角色與
名稱相同的元素。這時請重新快照，改用新的 ref。

### 分頁、對話框、下載、上傳

- **分頁**有固定的 id（`t1`、`t2`…）。點擊開出的分頁會成為使用中分頁，動作結果
  會說明這件事，並顯示它的文字。Chromium 已經通知要開新視窗時，動作會最多
  等 3 秒讓它開好再回傳。同時最多開 20 個分頁：`browser_tabs` 會拒絕再開新的，
  並提示先關掉一個；頁面超出上限開的分頁（或 `profile_dir` 在啟動時恢復的分頁）
  會被關閉並回報。
- **對話框。** `alert` 與 `beforeunload` 會自動接受並回報。`confirm` 或
  `prompt` 對話框會讓該次呼叫以「dialog pending」結束；在用 `dialog` 動作回答
  之前，其他瀏覽器呼叫都會回傳錯誤，說明有對話框在等待。
- **下載**存放在生物工作目錄（以下載開始時為準）下的 `browser-downloads/`，結果
  會給出存檔路徑。啟用 sandbox 時，路徑被其檔案寫入規則拒絕的下載，會在寫入任何
  東西之前被取消，並回報為被拒絕，附上原因。
  兩秒後還沒下載完的檔案會回報為進行中。在整個工作階段期間（包括瀏覽器重啟）
  最多開始 50 個下載、最多保留 1 GB 的下載檔案。超出數量的下載會被取消；會讓
  保留總量超過 1 GB 的下載會先完整下載、再被刪除。兩者都回報為失敗；5 分鐘後
  還沒存完的下載也會被取消並回報為失敗。
- **上傳**的路徑來自該次呼叫的 `files` 參數（相對於工作目錄），因此 sandbox
  會像檢查其他檔案讀取一樣檢查它們。藏在自訂樣式按鈕後面的檔案輸入框沒有自己的
  ref；把 `upload` 對準看得到的那個按鈕，工作階段會改走檔案選擇視窗。

### 瀏覽器保留什麼、何時重來

瀏覽器在第一次瀏覽器呼叫時啟動（`browser_tabs` 的 `list` 不會啟動它）。閒置
超過 `idle_close_s`、當機，或呼叫 `close_browser` 時，瀏覽器會關閉；不再回應的
瀏覽器會連同它啟動的所有行程一起被強制結束。下一次呼叫會啟動新的，並說明狀態
已遺失。除非設定了 `profile_dir`，新瀏覽器用的是用完即丟
的設定檔，所以登入狀態撐不過重啟。

設定了 `profile_dir` 時，只有在這個行程中曾在該設定檔上執行過的每個瀏覽器都已確認
結束（它啟動時的那些行程都已結束，或它自己的關閉流程已完成），才會啟動新的瀏覽器。
被強制結束的瀏覽器通常很快就會結束。如果 5 秒內無法確認它已結束，這次呼叫會失敗，
並回報一個指明該設定檔的錯誤，而不是在上面再啟動第二個瀏覽器（同一個設定檔上的兩個
Chromium 行程會損壞它）；之後的呼叫會再試一次。這適用於工作階段內的重啟、修改
options，以及停用後再啟用外掛。它只認得這個行程啟動的瀏覽器。用完即丟的設定檔不需要
等待。

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
| `secrets` | map | `{}` | 密鑰名稱，各自對應到 `{env: VAR_NAME, origins: [...]}`：存放它的環境變數，以及允許輸入它的網頁來源，供 `type` 動作使用。見[密鑰](#密鑰)。 |
| `nav_timeout_s` | number | `30` | 導覽逾時秒數（大於 0，最多 3600）。 |
| `action_timeout_s` | number | `5` | 每個動作與頁面讀取的逾時秒數（大於 0，最多 3600）。一整批 `browser_act` 最多 60 秒，若（此值 + 10 秒）的三倍更長則取後者。 |
| `snapshot_max_chars` | int | `20000` | 快照長度上限（1000–1000000）。被截斷的快照最後一行會說明省略了多少、該怎麼縮小範圍。 |
| `idle_close_s` | number | `600` | 超過這麼多秒沒有呼叫就關閉瀏覽器（大於 0，最多 86400）。 |

### 啟動

設了 `executable_path` 時只會試那一個瀏覽器；設了 `channel` 時只試那個 channel。
兩者都沒設時，外掛依序嘗試 Playwright 內附的 Chromium、已安裝的 Google Chrome、
Microsoft Edge。沒有主機資源的生物（見[多使用者主機](#多使用者主機)）只試內附的
Chromium。頁面一律使用固定的 viewport，device scale factor 為 1。

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
的主機）時，該動作會回報為失敗並指出主機與原因，其後的動作不會執行，結果會寫
`Navigation blocked` 以及頁面沒有載入。`browser_navigate` 也會一開始就拒絕非網頁的 scheme（`file:`、
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
外掛當下的狀態：是否啟用，以及目前的 options。改動不需要等下一次工具呼叫才生效。

- **新連線**在開啟那一刻依 sandbox 的規則判定。
- **已開的連線**只要還有連線開著，每 2 秒重新判定一次，每次瀏覽器呼叫時也會立刻
  判定。規則現在拒絕其主機的連線會被關閉，並在下一個結果裡列為被擋下；因此收緊
  `network_allowlist` 或改成 `network: deny`，會在約 2 秒內關掉它所拒絕的連線。

- **`network: deny`**：任何瀏覽器工具都不會執行。sandbox 會在呼叫到達瀏覽器之前
  就拒絕。
- **`network_allowlist`**：`browser_navigate` 與 `browser_tabs` 的 URL 參數會拿來
  比對，代理開出的每條連線（含子資源）也一樣。這份清單只寫主機名稱（沒有連接埠），
  並疊加在瀏覽器自己的 `allow_hosts` 之上；主機必須兩邊都通過。
- **`os_enforce: require`**：瀏覽器會拒絕啟動，因為 Chromium 無法在 sandbox 的
  子行程隔離下執行。這個設定生效時已在跑的瀏覽器，會在下一次瀏覽器呼叫（`browser_*`
  工具，或其他工具透過 `agent_session` 使用它，見下文）時關閉；在那之前它繼續執行，它的連線仍照網路規則判斷（每 2 秒
  一次的重新判斷不看 `os_enforce`）。瀏覽器被當成網路工具看待，所以 `syscall`
  等級不會擋它。
- **檔案路徑**：`browser_act` 的 `files`（讀取）與 `browser_screenshot` 的
  `path`（寫入）會經過和其他檔案工具相同的路徑檢查。
- **下載**在建立任何檔案之前，先依檔案寫入規則（`fs_write`、`fs_deny`）檢查；
  被拒絕的下載會被取消，並回報為被拒絕。
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

`browser_tabs` 要加閘門，因為帶 `url` 的 `new` 會開啟頁面。這裡不替
`browser_snapshot` 與 `browser_screenshot` 加閘門：它們讀取頁面（沒有瀏覽器在跑時
會啟動它），截圖的 `path` 是一次由 sandbox 檢查的檔案寫入。`gated_tools` 留空
代表每個工具都要批准，所以請明確列出來。

`browser_eval` 不只是讀取，還能改變頁面、發出請求。想在沒有它的情況下使用外掛，
就單獨關掉這個工具：`/module disable browser_eval`，或在 Python 裡
`agent.set_tool_enabled("browser_eval", False)`。外掛停用再啟用後它仍保持關閉，
並隨工作階段儲存（見[外掛](plugins.md)中的「工具來源與關閉工具」）。

## 密鑰

options 裡放的是參照，永遠不是值本身。每個密鑰寫明存放它的環境變數，以及允許
輸入它的網頁來源（origin）：

```yaml
plugins:
  - name: browser
    options:
      secrets:
        staging_pw:
          env: STAGING_PASSWORD
          origins: ["https://staging.example.com"]
```

來源由 scheme、主機與選用的連接埠組成，例如 `https://staging.example.com` 或
`http://localhost:5173`。帶路徑、萬用字元、空清單，或沒有 `origins` 的密鑰，都是
設定錯誤。

`type` 動作若指定 `secret: staging_pw`（取代 `text`），就會輸入
`STAGING_PASSWORD` 的值，這個值在動作執行時才從環境變數讀取；但只有元素所在文件
本身的來源列在清單上時才會輸入。frame 各自計算：允許的頁面裡內嵌的跨來源 frame
會被拒絕，除非它的來源也列在清單上。被拒絕的動作會失敗，原因裡寫明密鑰名稱與
違規的來源，絕不寫出值，例如
`refused: secret staging_pw may be typed only into https://staging.example.com; e7 is on https://evil.example`。

檢查與輸入是對同一個元素的同一步：工作階段只找一次元素，判斷這個元素本身所在文件
的來源，再輸入到同一個元素。頁面若在兩者之間換掉這個元素或讓它的文件跳轉，動作會
失敗，而不會把密鑰輸入到別處。不加 `slowly` 時，值由該文件裡的一段指令碼一次設定，
指令碼在設定前會再檢查一次文件的來源（欄位會收到 `input` 與 `change` 事件，但沒有
按鍵事件）。加上 `slowly` 時，值會逐鍵輸入到目前取得焦點的元素，所以在輸入過程中
移動焦點的頁面，可能在別處收到剩下的按鍵；只有欄位需要按鍵事件時，才對密鑰使用
`slowly`。

值的長度至少要四個字元。
在每個文字結果裡，它（包括原始值以及 URL、JSON 編碼後的寫法）都會顯示成
`[secret:staging_pw]`。密碼欄位也會遮蔽：在快照裡，密碼欄位的值顯示成
`(redacted)`（若是這個密鑰填入的，則顯示 `[secret:staging_pw]`）；同一個值出現在
結果其他地方（標題、URL）時顯示成 `[redacted]`。`autocomplete` 標明為密碼的欄位
也一樣（切換成顯示明文的密碼欄位通常保留這個屬性）。Chromium 的無障礙樹以明文
帶著密碼值，所以某個頁面上找出密碼欄位的檢查失敗時，所有文字欄位的值都顯示成
`(withheld)`，結果也會說明。

沒有主機資源的生物（見[多使用者主機](#多使用者主機)）不能使用 `secrets`：它們
指向的是伺服器上的環境變數。

仍然看得到密鑰的地方：

- **截圖。** 頁面若把這個值當成可見文字顯示出來，圖片裡就看得到。
- **`browser_eval`。** 指令碼以頁面的權限執行，讀得到欄位的值。牽涉到密鑰時，
  請用 `permgate` 替 `browser_eval` 加上閘門，或把它關掉。
- **網站本身**，這是當然的。來源限定決定的是密鑰可以交給哪個網站；密鑰輸入之後，
  它並不限制這個網站怎麼使用它。網站的指令碼，以及頁面載入的任何第三方指令碼，
  都能讀取這個欄位、把值送到任何地方，或把它顯示在頁面上。只在你信任的網站上
  輸入密鑰。

## 不受信任的頁面內容

所有由頁面決定的內容（文字、標題、元素名稱、對話框訊息、檔名、指令碼結果、頁面
給的錯誤訊息）都包在一個 `page_content` 元素裡，上面帶著這個工作階段固定的
隨機 id 與 `trust="untrusted"`；內容來自與結果第一行 URL 不同的來源（origin）
時（點擊開啟的分頁、分頁清單），還會加上 `origin` 屬性。內容裡任何看起來像結束標籤
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
Chromium，只能連公開位址，也不能使用密鑰。若生物的 options 要求更多
（`headless: false`、`profile_dir`、`executable_path`、`channel`、
`allow_loopback`、`allow_private` 或 `secrets`），每次瀏覽器呼叫都會在啟動任何
瀏覽器之前得到一個列出被拒 option 的錯誤。這樣的生物絕不會改用已安裝的 Chrome
或 Edge：沒有內附的 Chromium 時，錯誤會提示執行 `playwright install chromium`；
限制生效前已啟動的瀏覽器會在下一次呼叫時被關閉。單人使用的
主機、`kt run` 與 CLI 不受此限制。

## 在你自己的工具或程式碼裡使用瀏覽器

瀏覽器執行環境 `kohakuterrarium.browser` 是一個獨立的元件；插件與它的六個工具只是
它的一個使用者。自訂工具、腳本或測試都能直接使用同一個受管控的 Chromium，走同一個
代理與網路政策，不必經過那些工具。`import kohakuterrarium.browser` 很輕，絕不會
import Playwright；要等某個呼叫真的需要瀏覽器時才會啟動。

入口分成兩類。`kohakuterrarium.browser` 裡的**底層 API** 只套用瀏覽器自己的
options；除非你把框架的政策傳進去，否則它不套用任何框架政策：

- **自己的工作階段。** `async with browser.BrowserSession(options) as session:`
  得到一個別人碰不到的瀏覽器，區塊結束時關閉。沒給 `downloads_dir` 時，每個下載
  都會被取消，磁碟上不會寫入任何東西。
- **一次性讀取。** `await browser.read_page(url, options)` 啟動瀏覽器、讀取頁面，
  並在回傳前關閉瀏覽器。

**感知框架**的入口接收工具的 `ToolContext`，並套用和 `browser_*` 工具相同的檢查：

- **在插件檢查之下讀取。** `kohakuterrarium.builtins.plugins.browser.reading` 裡的
  `await read_page_for(context, url, options)` 在自己的瀏覽器裡讀取，不論這個生物
  有沒有啟用瀏覽器插件。sandbox 要求 OS 層級隔離，或 `options` 要求了沒有主機資源
  的生物不能用的東西（見[多使用者主機](#多使用者主機)）時，它在啟動任何東西之前就
  拒絕；這樣的生物只用內附的 Chromium。每個連線都依 sandbox 當下的網路規則判斷，
  也不寫入任何下載。讀取頁面的框架工具應該用這個呼叫。
- **Agent 共用的瀏覽器。** 在工具裡呼叫 `await browser.agent_session(context)`。
  它回傳 `browser_*` 工具所用的那個工作階段，先經過插件自己的檢查（sandbox、多使用者
  限制）；插件沒載入時回傳 `None`；檢查拒絕時拋出例外。你的呼叫和工具的呼叫一起
  排隊，並共用它們的瀏覽器狀態（見下文）。

`read_page`（工作階段上的或一次性的）是把頁面當成資料讀取的呼叫。它回傳一個
`PageRead`：最終的 `url`（跟隨重新導向之後）、HTTP `status`、`title`、`html`
（頁面腳本執行後的 DOM）與 `text`（body 算繪後的文字，也就是人看到的排版），每一
項都在 `max_chars`（預設 1 000 000）處截斷。設定的密鑰以及頁面密碼欄位的值會從
每一項中遮蔽。不做隱藏文字過濾，所以請把這些欄位當成不受信任的頁面內容。

讀取走的是與 `browser_navigate` 相同的 URL 檢查、代理與網路政策，出錯時拋出例外而
不是回傳錯誤文字：URL 被拒或重新導向被擋時拋 `NavigationBlockedError`，載入超過
`nav_timeout_s` 時拋 `BrowserTimeoutError`，載入失敗或 URL 只觸發下載時拋
`NavigationError`；讀取期間因 sandbox 現在拒絕而被代理切斷的連線也拋
`NavigationBlockedError`。它在一個生物永遠看不到的暫時分頁裡執行：不在分頁清單裡，
不改變目前的分頁與它的 ref，不論從哪條路徑結束都會被關閉。它開啟的對話框會被關掉
（dismiss），它開始的下載會被取消，它開啟的視窗會被關閉；這段期間生物的某個分頁
開啟的視窗照常成為分頁。

在 Agent 共用的工作階段上，讀取和生物的瀏覽**並不隔離**。暫時分頁和生物的分頁用同一
個瀏覽器設定檔：cookie、登入、同源儲存空間、快取與 service worker 都是共用的，所以
讀取會帶上生物的 cookie，而被造訪的網站做的事（設定 cookie、註冊 service worker、
修改它伺服器上的資料）之後也會留給生物的分頁。讀取自己的載入中被擋下的重新導向會
拋給呼叫端，不會回報給生物；讀取期間代理擋下的其他請求會出現在生物的下一個結果裡，
因為代理分不出是哪個頁面發出的。需要一個和生物什麼都不共用的讀取時，請用
`read_page_for`、一次性的 `read_page`，或你自己的工作階段。

框架工具這樣讀取：

```python
from kohakuterrarium import browser
from kohakuterrarium.builtins.plugins.browser.reading import read_page_for

async def summarize_page(url: str, context) -> str:
    page = await read_page_for(
        context, url, browser.BrowserOptions(allow_hosts=("docs.example.com",))
    )
    return page.text
```

底層呼叫接收 `external_check(host, port)`：回傳 `None` 表示允許連線，回傳一個原因
則拒絕；代理開啟的每個連線都會問它。sandbox 的 `network_policy` 執行環境服務正好是
這個形狀，所以傳入它的 `check` 會加上 sandbox 的網路規則，但也只有這些：這樣啟動的
底層讀取在 sandbox 要求 OS 層級隔離時照樣執行，對沒有主機資源的生物也照樣採用主機
相關的 options（可見視窗、系統 Chrome、loopback）。所有名稱與簽章見
[Python 參考](../reference/python.md#browser)。

## 限制與已知缺口

- 每個 `browser_act` 最多 20 個動作；`wait` 最多 10 秒，一批最多 60 秒
  （`action_timeout_s` 較長時更多）。
- 最多 20 個開著的分頁；`browser_tabs` 會拒絕再開新的，頁面超出上限開的分頁
  （或恢復的 profile 帶來的分頁）會被關閉並回報。
- `browser_eval` 的結果在 10000 個字元處截斷，指令碼必須在 `action_timeout_s`
  內完成。
- 截圖為 PNG；PNG 會超過約 500 KB 時改用 JPEG。仍然太大的圖片會被縮小，結果會
  給出把圖片座標換算回 CSS 像素的倍數；需要縮到某邊不足 200 像素的圖片不會回傳。
  整頁截圖與元素截圖最高 8000 像素，結果會寫明這一點。
- 每個動作之後，工作階段會等頁面安靜下來（150 ms 內沒有 DOM 變化，最多 2 秒）。
  它看不到尚未觸發的計時器：頁面稍後才更新時，請加一個 `wait` 等你預期的文字。
- **只有原生工具呼叫時，截圖才會送到模型。** 在文字型的工具呼叫格式（`bracket`、
  `xml`）下，工具結果是以文字送回去的，模型只拿得到截圖的文字那一行（擷取了什麼、
  存在哪裡），看不到圖片。截圖重要時請用 `tool_format: native`，或用 `path` 把截圖
  存下來。
- 每個生物一個瀏覽器；它的呼叫一次只跑一個。
- 只支援 Chromium，透過 Playwright。
- 下載上限約束的是工作階段保留的檔案。超過上限的檔案會先完整下載再刪除，所以它們
  不約束下載進行中的磁碟用量與流量；Chromium 自己的暫存下載檔、設定檔與快取也不在
  上限之內。
- 網路隔離靠的是過濾代理、DNS 釘選（每個連線都連到經過檢查的那個位址）與 Chromium
  的啟動參數。Chromium 以執行生物的使用者身分作為一般行程執行，沒有作業系統層級的
  行程、檔案系統或網路隔離。
- 瀏覽器是為受信任的本機使用者的 Agent 設計的。[多使用者](#多使用者主機)限制縮小了
  非管理員的生物能要求的範圍，但它們不是沙箱。多租戶服務，或你不信任的生物，需要在
  瀏覽器外再加一層更強的邊界：每個租戶一個獨立的作業系統使用者、容器或虛擬機，並有
  各自的儲存配額與對外網路控制。
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
- **「the browser profile … is in use by a browser that could not be confirmed
  to have exited」。** 使用該 `profile_dir` 的瀏覽器被強制結束了，但它的行程還沒
  結束。稍後再試；如果一直失敗，請結束仍在使用該設定檔目錄的 Chromium 行程。

## 延伸閱讀

- [參考 / 內建外掛](../reference/builtin-plugins.md#browser)：option 一覽表。
- [參考 / 內建項目](../reference/builtins.md#browser)：六個工具。
- [外掛](plugins.md)：外掛如何掛進 agent，包含 `sandbox` 與 `permgate`。
- [設定](configuration.md)：`plugins:` 在生物設定裡的位置。
