---
title: 子代理
summary: 設定內建與內聯子代理、執行期預算外掛和自動壓縮。
tags:
  - guides
  - sub-agent
  - budget
---

# 子代理

子代理是在同一個生物內部進行縱向委派的方式：父控制器像呼叫工具一樣呼叫一個專家，而這個專家擁有自己的對話、提示詞、允許使用的工具、外掛與預算。

當你希望父控制器保持輕量、專注於編排，而由專家負責探索、規劃、修改、審查、研究、摘要或產生最終使用者回應時，就使用子代理。

## 內建子代理

框架內建以下設定：

- `explore`
- `plan`
- `worker`
- `critic`
- `research`
- `coordinator`
- `memory_read`
- `memory_write`
- `summarize`
- `response`

按名稱引用：

```yaml
subagents:
  - explore
  - plan
  - worker
```

上面的字串簡寫等價於：

```yaml
subagents:
  - name: explore
    type: builtin
```

內建子代理已經啟用自動壓縮和統一執行期預算外掛：

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # 無 walltime_budget
```

每個預算軸的列表/元組形狀是 `[soft, hard]`。軟限制會在子代理下一輪 LLM 呼叫前注入提醒；硬限制會阻止繼續派發工具/子代理，讓專家用文字收尾，而不是繼續消耗執行預算。

## 覆蓋內建預算

對內建子代理，透過條目的 `options` 覆蓋 `plugins`：

```yaml
subagents:
  - name: worker
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [60, 90]
            tool_call_budget: [120, 180]
```

只有在確實需要依牆鐘時間截斷時才設定 `walltime_budget`：

```yaml
subagents:
  - name: research
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [80, 120]
            tool_call_budget: [150, 220]
            walltime_budget: [300, 600]
```

多數長任務更適合使用 turn/tool 預算，而不是 walltime，因為模型和服務商延遲差異很大。

## 純 YAML 內聯子代理

很多子代理不需要 Python 模組。使用不帶 `module`/`config` 的 `type: custom`，並把 `SubAgentConfig` 欄位直接寫在 YAML 中：

```yaml
subagents:
  - name: dependency_mapper
    type: custom
    description: Map dependency edges without editing files
    system_prompt: |
      You map imports and runtime dependencies. Return a compact graph and
      cite files as path:line when possible.
    tools: [glob, grep, read, tree]
    can_modify: false
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

內聯設定支援與 `SubAgentConfig` 相同的欄位，包括：

- `system_prompt`, `prompt_file`, `extra_prompt`, `extra_prompt_file`
- `tools`, `can_modify`, `interactive`, `output_to`, `output_module`
- `default_plugins`, `plugins`
- `compact`
- `model`, `temperature`
- `budget_inherit`, `budget_allocation`

執行期預算軸（`turn_budget`、`tool_call_budget`，以及可選的 `walltime_budget`）寫在 `budget` 外掛的 `options` 中；它們不是核心 `SubAgentConfig` 欄位。

只有當你想在套件之間共享設定物件、以程式方式建構提示詞，或替換執行期行為時，才需要 Python 模組。

## 執行期外掛與外掛包

預算執行是基於外掛的。內建執行期表面如下：

| 名稱 | 類型 | 何時使用 |
|---|---|---|
| `budget` | 外掛 | 需要 turn/tool/walltime 預算計數與執行；預算軸寫在 `options` 下。 |
| `compact.auto` | 外掛 | 已設定 `compact`，並希望在 LLM 輪次後自動檢查壓縮。 |
| `auto-compact` | 外掛包 | 想透過 `default_plugins` 啟用 `compact.auto`。 |

自訂或內聯子代理需要明確加入預算與自動壓縮：

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the proposed change."
    tools: [read, grep]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

同名的使用者宣告外掛會覆蓋預設外掛，因此子代理可以替換成更大或更小的預算設定。

## 父代理預算與子代理預算

`budget` 外掛中的 `turn_budget` 和 `tool_call_budget` 選項是該子代理執行的獨立多軸預算。

舊的父級共享迭代預算仍然存在：

```yaml
max_iterations: 100
subagents:
  - name: explore
    type: builtin
    options:
      budget_inherit: true      # 預設：存在父級迭代預算時共享
  - name: critic
    type: builtin
    options:
      budget_allocation: 10     # 單獨的舊式 10-turn 切片
```

新設定優先使用明確的 `plugins: [{name: budget, options: ...}]` 加 `default_plugins: ["auto-compact"]`。只有在你想讓父代理和子代理共享同一個全域上限時，才使用 `max_iterations`。

## 子代理自動壓縮

子代理也可以有自己的壓縮設定：

```yaml
subagents:
  - name: long_research
    type: custom
    system_prompt: "Research deeply, then summarize."
    tools: [web_search, web_fetch, read]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [80, 120]
          tool_call_budget: [150, 220]
    compact:
      max_tokens: 120000
      threshold: 0.75
      target: 0.40
      keep_recent_turns: 4
```

`compact.auto` 外掛會在 LLM 輪次後檢查用量，並在達到閾值時觸發壓縮。沒有 `compact.auto`（或 `auto-compact` 外掛包）時，單獨的 `compact:` 只會設定管理器，不會自動觸發。

## 快速檢查表

對於每個需要實際工作的非內建子代理：

1. 只給它必要工具。
2. 如果它有 `compact:` 設定，加上 `default_plugins: ["auto-compact"]`。
3. 加上 `budget` 外掛，並至少設定 `turn_budget: [40, 60]` 和 `tool_call_budget: [75, 100]`。
4. 除非確實需要牆鐘截斷，否則避免 `walltime_budget`。
5. 提示詞保持專家化，並要求返回緊湊的結構化結果。
