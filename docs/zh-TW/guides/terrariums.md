---
title: 生態瓶
summary: 用頻道、輸出接線、root agent、熱插拔、觀察模式做橫向多代理。
tags:
  - guides
  - terrarium
  - multi-agent
---

# 生態瓶

給想把多隻生物組起來合作的讀者。

**生態瓶** 是托管行程內所有運行中生物的執行期引擎。一隻獨立 agent 就是引擎裡的 1-creature graph；多代理團隊則是用頻道連起來的 connected graph。引擎負責生命週期、共用頻道、熱插拔、以及框架層級的**輸出接線** — 把一隻生物回合結束的輸出自動送到指定目標。引擎本身沒有 LLM、不做決策 — 純粹接線。生物本身不知道自己在生態瓶裡 — 它們只知道自己 listen 哪些頻道名字、能送到哪些頻道名字，而引擎讓那些名字變成真的。

`terrarium.yaml` 設定檔則成為一份 **recipe**：「加這些生物、宣告這些頻道、接這些邊」的序列，套用到引擎上。它不再是一種獨立的實體。

觀念預備：[生態瓶](../concepts/multi-agent/terrarium.md)、[root agent](../concepts/multi-agent/root-agent.md)、[頻道](../concepts/modules/channel.md)。

我們把生態瓶當作橫向多代理的**提案架構** — 這些零件湊得起來 (接線 + 頻道 + 熱插拔 + 觀察 + 向 root 回報 lifecycle)，kt-biome 的四個生態瓶把它們完整跑過一輪。還在摸索的是慣用寫法；看下面的 [如實定位](#如實定位) 與 [ROADMAP](../../ROADMAP.md)。

## 設定結構

```yaml
terrarium:
  name: swe-team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md    # 該團隊專屬的派工 prompt，跟生態瓶放一起
  creatures:
    - name: swe
      base_config: "@kt-biome/creatures/swe"
      output_wiring: [reviewer]            # 決定性邊：每次 swe 回合結束 → reviewer
      channels:
        listen:   [tasks, feedback]
        can_send: [status]
    - name: reviewer
      base_config: "@kt-biome/creatures/swe"
      system_prompt_file: prompts/reviewer.md   # reviewer 角色用 prompt 表達，不另開生物
      channels:
        listen:   [status]
        can_send: [feedback, results, status]  # 條件式：通過 → results、退件 → feedback
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
    results:  { type: queue }
    status:   { type: broadcast }
```

- **`creatures`** — 跟獨立生物一樣的繼承與覆寫規則。多出 `channels.listen` / `channels.can_send`，加上選用的 `output_wiring`。
- **`channels`** — `queue` (每則訊息一個消費者) 或 `broadcast` (每個訂閱者都收到)。
- **`output_wiring`** — 每隻生物的目標清單，回合結束時自動收到這隻生物的輸出。見 [輸出接線](#輸出接線)。
- **`root`** — 選用的面向使用者生物，坐在生態瓶外面；見下。kt-biome 不附通用 `root` 生物 — 每個生態瓶自帶 `prompts/root.md`。

頻道描述的簡寫：

```yaml
channels:
  tasks: "work items the team pulls from"
```

欄位參考：[reference/configuration](../reference/configuration.md)。

## 自動建立的頻道

執行期一定會建：

- 每隻生物一條以它名字命名的 `queue` 頻道，讓別人可以 DM 它。
- 設了 `root` 時，多一條 `report_to_root` queue。

這些不用自己宣告。

## 頻道怎麼接起來

每隻生物的每個 `listen:` 項目，執行期會註冊一個 `ChannelTrigger`，訊息到的時候叫醒控制器。System prompt 會收到一小段拓樸描述，告訴生物自己 listen 哪些頻道、可以送到哪些。

`send_message` 工具會自動加上去；生物用 `channel` 與 `content` 參數呼叫它。預設 bracket 格式長這樣：

```
[/send_message]
@@channel=review
@@content=...
[send_message/]
```

生物如果用 `tool_format: xml` 或 `native`，呼叫的樣子不一樣、語意相同。見 [撰寫生物 — 工具格式](creatures.md)。

## 跑生態瓶

```bash
kt terrarium run @kt-biome/terrariums/swe_team
```

旗標：

- `--mode tui|cli|plain` (預設 `tui`)
- `--seed "Fix the auth bug."` — 往 seed 頻道注入一則啟動訊息
- `--seed-channel tasks` — 指定哪條頻道收 seed
- `--observe tasks review status` / `--no-observe` — 頻道觀察
- `--llm <profile>` — 覆寫每隻生物的 LLM
- `--session <path>` / `--no-session` — 持久化

TUI 模式會有多 tab 介面：root (有的話)、每隻生物、被觀察的頻道。CLI 模式會把第一隻生物 (或 root) 掛到 RichCLI 上。

只看生態瓶資訊不執行：

```bash
kt terrarium info @kt-biome/terrariums/swe_team
```

## Root agent 模式

Root 是一隻獨立生物，掛了生態瓶管理工具。它坐在生態瓶**外面**、從上面驅動裡面：

- 自動 listen 每一條生物頻道。
- 收 `report_to_root`。
- 拿到生態瓶工具 (`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`…)。
- 自動收到一段產生的「生態瓶概況」prompt，列出綁定團隊的生物與頻道。
- 生態瓶跑 TUI/CLI 時，它就是面向使用者的介面。

想要一個單一對話介面時用 root；純背景合作的流程就不用。

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # 該團隊專屬的派工 prompt
```

kt-biome 不附通用 `root` 生物。每個生態瓶自己擁有 `root:` 區塊與對應的 `prompts/root.md` — prompt 可以直接點名真實的團員 (「寫程式 → 送到 `driver`」)，因為它住在它 orchestrate 的團隊旁邊。框架會自動提供管理工具組與拓樸概況。

設計理由請看 [concepts/multi-agent/root-agent](../concepts/multi-agent/root-agent.md)。

## 執行期熱插拔

從 root (透過工具) 或寫程式直接對引擎操作：

```python
from kohakuterrarium import Terrarium

async with Terrarium() as engine:
    await engine.apply_recipe("@kt-biome/terrariums/swe_team")
    tester = await engine.add_creature(
        "@kt-biome/creatures/swe", creature_id="tester",
    )
    # tester 落在自己的 singleton graph；connect() 會把它合進來。
    swe = engine["swe"]
    result = await engine.connect(swe, tester, channel="review")
    # result.delta_kind == "merge"
```

跨 graph 的 `connect()` 會合併兩個 graph — environment 取聯集，掛著的 session store 合成一份，新的 listener 會被注入 `ChannelTrigger`。`disconnect()` 可能把 graph 拆回兩邊、並把 parent session 複製到兩側。參考 [`examples/code/terrarium_hotplug.py`](../../examples/code/terrarium_hotplug.py)。

Root 用的對應工具：`creature_start`、`creature_stop`、`terrarium_create`、`terrarium_send`。

熱插拔很適合臨時補一個專員、又不用重啟。既有頻道會自動吸收新的 listener；新生物會在它的 system prompt 看到自己的頻道拓樸。

## 觀察模式 (debug 用)

`ChannelObserver` 是任何頻道上的非破壞性觀察點。跟一般消費者不一樣，observer 讀訊息不會跟 queue 消費者競爭。Dashboard 底下用這個；寫程式的話：

```python
sub = runtime.observer.observe("tasks")
async for msg in sub:
    print(f"[tasks] {msg.sender}: {msg.content}")
```

`kt terrarium run` 的 `--observe` 會對清單上的頻道掛 observer，在 TUI 裡串流出來。

## 程式化生態瓶

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        # 用 id 找到引擎裡某隻生物
        async for chunk in engine["swe"].chat("Fix the auth bug."):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

更多寫法 (事件訂閱、熱插拔、獨立 + recipe 共存) 看 [程式化使用](programmatic-usage.md)，以及 [`examples/code/`](../../examples/code/) 裡可執行的腳本 (`terrarium_solo.py`、`terrarium_recipe.py`、`terrarium_hotplug.py`)。

新程式碼請用 `Terrarium`。

## 輸出接線

頻道靠生物**記得**呼叫 `send_message`。對那種確定性的 pipeline 邊 — 「每次 coder 寫完，runner 就要跑它寫的東西」 — 框架提供另一條路：**輸出接線 (output wiring)**。

生物在 config 宣告自己回合結束的輸出要送去哪。每個回合邊界，框架會對每個目標的事件佇列發一個 `creature_output` `TriggerEvent`。不用 `send_message`、不用 `ChannelTrigger`、中間也沒頻道。

```yaml
# terrarium.yaml 的 creature 區塊
- name: coder
  base_config: "@kt-biome/creatures/swe"
  output_wiring:
    - runner                              # 簡寫 = {to: runner, with_content: true}
    - { to: root, with_content: false }   # lifecycle ping (只帶 metadata)
  channels:
    listen: [reverts, team_chat]
    can_send: [team_chat]
```

完整欄位結構在 [reference / configuration — output wiring](../reference/configuration.md#output-wiring)。重點屬性：

- **`to: <creature-name>`** 指同一個生態瓶裡的另一隻生物。
- **`to: root`** 是魔術字串 — 指向坐在生態瓶外面的 root 代理。做 lifecycle ping 很好用；就算 root 沒在 listen 頻道也看得到。
- **`with_content: false`** 送過去的事件 `content` 是空的 — 純粹是「回合結束了」的 metadata 訊號。
- **`prompt` / `prompt_format`** 客製接收端的 prompt-override 文字。

### 什麼時候接線、什麼時候用頻道

以下情況用 **輸出接線**：

- 這條邊是決定性的 — 某隻生物的輸出永遠往下一站。
- 你要 lifecycle 觀察，但又不想生物自己記得呼叫 `send_message`。
- Pipeline 是線性的 (或是迴圈型、但迴圈回頭仍然無條件)。

以下情況留在 **頻道**：

- 這條邊是條件式的。Reviewer 通過或退件；analyzer 保留或丟棄。接線不能分支，頻道可以。
- 流量是廣播 / status / team-chat — 選擇性、多人觀察。
- 你要的是 group-chat 形狀：多人可送、多人可聽。

同一個生態瓶裡兩種機制可以自由搭配。kt-biome 的 `auto_research` 在線性邊 (ideator → coder → runner → analyzer) 用接線，在 analyzer 的保留/丟棄決定與 team-chat status 用頻道。

### 接收端看到接線事件時會怎樣

事件會落進目標生物的事件佇列，走跟其他觸發器一樣的 `_process_event` 路徑。TUI 上接收端的 tab 會照一般回合的樣子渲染 (prompt 注入、LLM 文字、工具)。註冊在接收端的外掛透過既有的 `on_event` hook 看得到這個事件 — 沒有新的外掛 API。

## 如實定位

兩種合作機制已經能涵蓋今天大多數團隊：頻道 (工具 + 觸發器，自願) 與輸出接線 (框架層、自動)。kt-biome 的生態瓶把兩個都跑過 — 確定性 pipeline 邊用接線，條件式分支與 team-chat 流量用頻道。

還在摸索的是慣用寫法。Observer 面板與 TUI 對接線事件的呈現，比對頻道流量薄。條件式邊還是得走頻道，因為接線不能分支 — 要不要加個小小的 `when:` filter，我們想透過實際使用慢慢弄清楚，而不是先設計出來。內容模式 (`last_round` 與 `all_rounds` 與 summary) 之後或許對想把草稿推理一起帶著走的 pipeline 有用；目前不確定。開放問題整組在 [ROADMAP](../../ROADMAP.md)。

當一個 parent 可以自己拆解的時候，**子代理** (單一生物內的垂直派工) 更單純 — 對大多數「我只是想要 context 隔離」的直覺來說，這才是比較簡單的答案。只有當你真的想要不同生物各自合作、而且希望這些生物還能保持可以獨立執行的 config 時，才伸手去碰生態瓶。

## 疑難排解

- **團隊卡住、沒人傳訊息。** 最常見原因：寄件方靠 `send_message`，但 LLM 忘記呼叫。兩種解：
  - 對確定性 pipeline 邊加 `output_wiring:` — 框架不會忘。
  - 條件式邊必須留在頻道的話，就加強寄件方 prompt 對該頻道的提醒。
  用 `--observe` 即時看頻道流量。
- **生物沒對頻道訊息做反應。** 確認 `listen` 有這個頻道名字、`ChannelTrigger` 有註冊 (`kt terrarium info` 會印出接線)。
- **Root 看不到生物在幹嘛。** 兩條路：把 `report_to_root` 加進該生物的 `can_send` (走頻道)；或把 `{to: root, with_content: false}` 加進它的 `output_wiring` (走框架層 lifecycle ping；就算生物不呼叫 `send_message` 也會觸發)。
- **接線目標沒收到東西。** 確認目標生物在同一個生態瓶、且正在跑。接線以生物名字 (或魔術字串 `root`) 解析；不存在或停掉的目標會被 log 下來然後跳過。
- **生物很多的時候啟動很慢。** 每隻生物各自起自己的 LLM provider 與 trigger manager；啟動時間大致隨生物數線性增加。

## 延伸閱讀

- [撰寫生物](creatures.md) — 每一條生態瓶 entry 都是一隻生物。
- [組合代數使用指南](composition.md) — 只需要小迴圈、不需要整個生態瓶的時候，Python 端的替代方案。
- [程式化使用](programmatic-usage.md) — `Terrarium` 引擎。
- [概念 / 生態瓶](../concepts/multi-agent/terrarium.md) — 生態瓶為什麼長這樣。
