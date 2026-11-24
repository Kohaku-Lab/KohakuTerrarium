---
title: 浏览器
summary: 用内置的 browser 插件给 Creature 一个真正的 Chromium：安装、启用、六个浏览器工具、网络策略、sandbox 与 permgate、密钥、限制。
tags:
  - guides
  - browser
  - plugins
  - tools
---

# 浏览器

给想让 Creature 操作真实浏览器的读者：登录测试站点、填写表单、走完一整条操作流程，或者检查它刚做好的网页界面。

内置的 `browser` 插件为 Creature 提供一个常驻的 Chromium，以及六个用来驱动它的 `browser_*` 工具。浏览器在多次调用之间保持状态（cookie、登录、已打开的标签页）；模型以文本形式读取页面，页面元素带有 ref，模型就按 ref 操作；页面发出的每一个请求都要经过一个过滤代理，避免触及你的内网。

它和 `web_fetch` 不同：`web_fetch` 只渲染一个页面并返回文本，没有会话，也不能交互。只需要读内容时用 `web_fetch`；Creature 需要在网站上"做事"时，用 browser 插件。

概念先读：[插件](../concepts/modules/plugin.md) 与 [工具](../concepts/modules/tool.md)。浏览器由插件持有，工具也由插件提供，框架核心里没有任何与它相关的代码。

## 安装

浏览器是可选的 extra。没装它时框架照常启动，只有浏览器工具会失败，并在报错里说明要装什么。

```bash
pip install "kohakuterrarium[browser-use]"
playwright install chromium
```

第一行安装 Playwright，第二行下载 Playwright 所驱动的 Chromium。没有下载这份 Chromium 时，插件会依次退而使用已安装的 Google Chrome、Microsoft Edge（见下文「启动顺序」）。`kohakuterrarium[full]` 包含 `browser-use`；较早的 `browser` extra 是 `web_fetch` 背后的 Crawl4AI 渲染器，不会安装 Playwright。

## 在 Creature 上启用

插件已登记在内置目录中，**默认关闭**：插件打开之前，Creature 拿不到任何浏览器工具。

### 在配置中

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true      # 访问 localhost 上的开发服务器
```

不写 `options` 时全部使用默认值，浏览器只能访问公网站点。浏览器在第一次调用浏览器工具时才启动，而不是在 Creature 启动时。

### 运行时

每个 Creature 都能发现这个插件，所以可以在运行中的会话里打开它：在聊天里输入 `/plugin enable browser`，或者使用网页 UI 的 Plugins 标签页，那里也能编辑它的 options。修改后的 options 从下一次浏览器调用开始生效：当前浏览器关闭，再用新 options 启动一个新的。

停用插件会立即关闭浏览器，工具也会从 Creature 上消失；重新启用后，下一次调用会启动一个全新的浏览器（标签页、cookie、登录都不会保留）。

### 在 Python 中

要么把这条配置写进你用来构建的 config，要么直接传入插件实例：

```python
from kohakuterrarium import Agent
from kohakuterrarium.builtins.plugins.browser.plugin import BrowserPlugin

agent = await Agent.build(
    "path/to/creature",
    plugins=[BrowserPlugin(options={"allow_loopback": True})],
)
```

使用 `plugins=[{"name": "browser", "options": {...}}]` 的 `AgentConfig` 效果相同。

## 六个工具

| 工具 | 类别 | 用途 |
|---|---|---|
| `browser_navigate` | 外连 | 打开一个 `url`（`action` 为 `goto`，这是默认值），或执行 `back` / `forward` / `reload`，这三者会忽略 `url`。返回页面头部信息以及带 ref 的页面文字（页面超过 8000 个字符时只列可交互元素）。 |
| `browser_snapshot` | 读取 | 以带 ref 的无障碍树读取当前页面。可用 `ref`（只看一个子树）、`depth`（0 表示不限）、`query`（匹配某段文字的行及其祖先）或 `interactive`（只看控件）缩小范围；`boxes` 会给每个元素加上以视口像素表示的位置。 |
| `browser_act` | 修改 | 在一个标签页上按顺序执行一批 `actions`。每个动作返回一行状态，并附上页面发生了什么变化。`files` 列出上传动作要用的路径。 |
| `browser_screenshot` | 读取 | 把视口、单个元素（`ref`，优先于 `full_page`）或整个页面（`full_page`）截成图片；`path` 会同时存成文件。 |
| `browser_tabs` | 会话 | `list`（默认）、`new`（可带 `url`）、`select` 或 `close` 某个标签页，或者 `close_browser`。 |
| `browser_eval` | 脚本 | 在页面中运行一段 JavaScript 表达式或函数，可选地作用于某个元素 `ref`，返回其 JSON 结果。 |

除 `browser_tabs` 外，每个工具都接受可选的 `tab`（如 `t2` 这样的标签页 id；默认为当前活动标签页）。工具按风险拆分，这样按名称把关的 `permgate`、`sandbox` 就能区分"读页面""改页面"和"在页面里跑脚本"。

必须填写每个参数的模型（严格模式的函数调用）可以给用不到的参数传空值：`""`、`0`、`false` 或 `[]` 都表示"未提供"。

### 批量动作

`browser_act` 接受一个动作对象列表。每个对象有一个 `kind`，以及下表中的字段。

| kind | 字段 |
|---|---|
| `click` | `ref`，或以视口像素表示的 `x` / `y`；`button`、`double`、`modifiers` |
| `type` | `ref`；`text`，或 `secret`（已配置的密钥名）；`submit`、`slowly` |
| `select` | `ref`、`values`（选项文字） |
| `check` | `ref`、`checked`（默认 true） |
| `press` | `key`，如 `Enter` 或 `Control+A`；可选 `ref` |
| `hover` | `ref`，或 `x` / `y` |
| `scroll` | `ref`（滚动到可见），或 `dx` / `dy` 像素 |
| `drag` | 起点 `ref` 或 `x` / `y`；终点 `to`（一个 ref）或 `to_x` / `to_y` |
| `upload` | `ref`、`files`（指向本次调用 `files` 列表的下标） |
| `wait` | `text`、`text_gone`、`seconds` 三者之一 |
| `dialog` | `accept`（默认 true），prompt 对话框用 `text` |

动作用不到的字段可以传空值。两个字段都可能生效时：非空的 `ref` 优先于 `x` / `y`（`0, 0` 表示没有坐标），`secret` 优先于 `text`（空的 `text` 会清空输入框），`wait` 依次取 `text`、`text_gone`、大于 0 的 `seconds`。给属于别种动作的字段传了实际值会被拒绝。

在文本工具格式下，所有参数都以字符串传入，因此 `actions` 也可以是一个 JSON 字符串，或者直接写在调用正文里。

## 这些工具怎么配合

这些工具围绕一个循环设计：**读页面，再按 ref 批量操作，然后读结果。**

1. **打开页面。** `browser_navigate` 返回一行头部信息（`[t1]` 加 URL）和页面文字，也就是 `browser_snapshot` 给出的同一棵清理过的树，每个控件带一个 ref。页面超过 8000 个字符时只列可交互元素，并附一句说明文字被省略。
2. **需要时再读。** `browser_snapshot` 给出完整页面：标题、文字、控件。页面很大时，用 `query` 或 `interactive` 缩小范围，不要整页读完。ref 的样子是 `e12`；标签页载入新页面后会带前缀，例如 `f1e12` 或 `f2e12`，frame 内的元素带该 frame 的前缀。ref 请照原样使用。ref 只出现在用得上的地方：可交互元素（包括可点击的，或带 checked、expanded、pressed、selected 状态的元素）、frame、对话框、表格、行、article 以及有名称的容器；没有名称的地标和包装层（如 `main`、`list`）没有 ref，从未显示过的 ref 会被拒绝。frame 显示的 origin 与页面不同时，会写在它那一行上，例如 `- iframe [ref=e12] [origin=https://pay.example]:`。
3. **一次批量操作。** 把同一个意图的所有步骤放进一次 `browser_act`：输入名字、选套餐、勾选框、点击保存，再 `wait` 确认文字出现。动作按顺序执行，遇到第一个失败就停下，其余的报告为未执行。之所以要"一个意图一次调用"，是因为同一回合里的多个工具调用是并发执行的，分开调用无法表达"先点、再输入，点失败就停"；而且每次和模型往返，都是浏览任务里最慢的环节。
4. **读结果。** 在同一页面上的一批动作，结果是变化：自批量操作前那次快照以来新增（`+`）或改变（`~`）的行、换了位置的元素（标为 `(moved)`）、URL 和标题的变化、新开的标签页、对话框、下载以及被拦截的请求，或是 `No change in the page snapshot.`。如果变化会隐藏的比显示的多（筛选掉大部分行、关掉遮罩、变化被截断），结果改为显示整个页面。如果这批动作跳到了新页面或打开了标签页，就像 `browser_navigate` 一样显示那个页面的文字。在报告成功之前，先在结果里确认你想要的结果确实出现了。
5. **长页面与懒加载。** 要到达很长或懒加载页面的底部，用较大的 `dy` 做 `scroll`（或 `press` `End`），`wait` 新内容出现，重复直到结果显示 `No change in the page snapshot.`。还没有内容的页面会说明它可能仍在加载；`wait` 你期待的文字后再读一次。
6. **退回到像素。** 没有 ref 的东西（canvas、图表、自定义的拖放区域），用 `browser_screenshot` 看；`click`、`hover`、`drag` 也接受 `x` / `y` 坐标。截图像素就是设备缩放为 1 时的 CSS 像素，所以从图上读出的坐标就是要点击的坐标。带 `boxes` 的 `browser_snapshot` 会以文本形式给出同样的坐标。

ref 只对模型最后一次看到的页面有效。每次批量操作前，会话都会重新拍一次快照：如果某个 ref 指向的元素已经消失，或现在指向了另一个元素，会立刻拒绝，并在报错里点名这个 ref，而不是等到超时。此时重新快照，改用新的 ref 即可。

### 标签页、对话框、下载、上传

- **标签页** 有稳定的 id（`t1`、`t2`……）。点击打开的新标签页会成为活动标签页，动作结果会说明这一点，并显示它的文字。如果 Chromium 已经通知要开新窗口，动作会最多等 3 秒让它打开再返回。
- **对话框。** `alert` 和 `beforeunload` 会自动接受并在结果中报告。`confirm` 或 `prompt` 对话框会让调用返回 "dialog pending"；在用 `dialog` 动作回答之前，其他所有浏览器调用都会报错，提示有对话框在等待。
- **下载** 保存在 Creature 工作目录下的 `browser-downloads/` 中，结果会给出保存路径。两秒后仍在进行的下载会被报告为进行中。每个浏览器最多保留 50 个下载、总共 1 GB，之后的下载会被拒绝并报告。
- **上传** 的路径来自本次调用的 `files` 参数（相对于工作目录），所以 sandbox 会像检查其他文件读取一样检查它们。藏在样式化按钮后面的文件输入框本身没有 ref；把 `upload` 指向那个可见按钮，会话会通过文件选择框完成上传。

### 浏览器保留什么，何时重来

浏览器在第一次浏览器调用时启动（`action` 为 `list` 的 `browser_tabs` 不会启动它）。闲置超过 `idle_close_s`、崩溃，或执行 `close_browser` 时，它会关闭；下一次调用会启动一个新的，并说明状态已丢失。除非设置了 `profile_dir`，新浏览器用的都是一次性 profile，所以登录状态不会跨重启保留。

Creature 停止、插件被卸载或停用时，浏览器永久关闭。之后的调用会直接失败，绝不会在背后悄悄再启动一个。

Creature 的子代理共用它的浏览器。Creature 与子代理的调用在浏览器上排队，一次执行一个。

## Options

所有 option 及其默认值。未知的 option 名会在构建 Creature 时被拒绝。

| Option | 类型 | 默认 | 用途 |
|---|---|---|---|
| `headless` | bool | `true` | 不显示窗口运行。设为 `false` 可以看着浏览器干活。 |
| `channel` | string | 无 | 使用已安装的浏览器 channel，例如 `chrome` 或 `msedge`。 |
| `executable_path` | string | 无 | 要运行的 Chromium 系浏览器的路径。 |
| `viewport` | `"WIDTHxHEIGHT"` 或 `[w, h]` | `1280x800` | 页面尺寸（CSS 像素）；宽 320–7680，高 200–4320。 |
| `profile_dir` | string | 无 | 持久 profile 目录。不设时每次启动都用全新的一次性 profile。 |
| `allow_hosts` | list | `[]` | 非空时，页面只能访问这些主机。 |
| `block_hosts` | list | `[]` | 页面永远不能访问的主机。先于 `allow_hosts` 检查。 |
| `allow_loopback` | bool | `false` | 允许 `localhost` 及其他回环地址（本地开发服务器）。 |
| `allow_private` | bool | `false` | 允许内网地址（10/8、172.16/12、192.168/16、运营商级 NAT、IPv6 唯一本地地址）。 |
| `secrets` | map | `{}` | 密钥名到 `env:VAR_NAME` 引用的映射，供 `type` 动作使用。见下文「密钥」。 |
| `nav_timeout_s` | number | `30` | 导航超时（秒，大于 0，最多 3600）。 |
| `action_timeout_s` | number | `5` | 每个动作和每次读取页面的超时（秒，大于 0，最多 3600）。 |
| `snapshot_max_chars` | int | `20000` | 快照长度上限（1000–1000000）。被截断的快照末尾会有一行，说明省略了多少以及如何缩小范围。 |
| `idle_close_s` | number | `600` | 这么多秒没有调用就关闭浏览器（大于 0，最多 86400）。 |

### 启动顺序

设置了 `executable_path` 时只尝试那一个浏览器；设置了 `channel` 时只尝试那个 channel。两者都没设时，插件依次尝试 Playwright 自带的 Chromium、已安装的 Google Chrome、Microsoft Edge。页面始终使用固定视口，设备缩放比例为 1。

## 网络策略

浏览器的全部流量都经过会话自己持有的、监听在 127.0.0.1 上的过滤代理。Chromium 被设置为连回环流量也走这个代理，QUIC 被关闭，WebRTC 也不能在代理之外发送 UDP。代理能看到每一个请求：重定向的每一跳、子资源、弹出窗口、service worker 发出的请求以及 WebSocket 握手。因此页面无法靠重定向或在后台加载，去访问被拦截的地址。

对每个连接，代理会：

1. 只接受 `http` 和 `https`；
2. 拒绝 `block_hosts` 中的主机；当 `allow_hosts` 非空时，再拒绝不在其中的主机；
3. 当 sandbox 插件启用时，询问它（见下文）；
4. 解析主机名并拒绝特殊地址：回环地址（除非 `allow_loopback`）；内网、运营商级 NAT 和 IPv6 唯一本地地址（除非 `allow_private`）；以及一律拒绝的链路本地地址（包括云元数据地址 `169.254.169.254`）、未指定地址、组播和保留地址段。嵌在 IPv6 地址里的 IPv4 地址按那个 IPv4 地址判断；
5. 连接到它检查过的那个地址，所以第二次 DNS 应答无法把连接引到别处。

被拦截的请求会收到代理返回的 403，下一次工具结果会在 `Blocked N requests` 下列出它及原因。如果一次导航的主页面（或其重定向链上的任何主机）被拦截，就会变成一个工具错误，写明 URL 和原因。如果 `browser_act` 的某个动作导向被拦截的页面（链接或表单重定向到被拦截的主机），该动作会报告为失败并写明主机与原因，结果会写 `Navigation blocked` 以及页面没有载入。`browser_navigate` 还会直接拒绝非 web 协议（`file:`、`javascript:`……）和内嵌账号密码的 URL。

**本地开发服务器。** 回环地址默认被拦截。要测试本地应用，只需一个 option：`allow_loopback: true`。如果还想同时挡住整个互联网，再加上 `allow_hosts`：

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["localhost", "127.0.0.1"]
```

**主机模式。** `allow_hosts` 和 `block_hosts` 的条目写法：

| 模式 | 匹配 |
|---|---|
| `example.com` | 恰好是 `example.com`，任意端口 |
| `*.example.com` | `example.com` 的任意子域名（不含 `example.com` 本身） |
| `example.com:8443` | 只匹配 8443 端口上的 `example.com` |
| `127.0.0.1:5173` | 只匹配该地址的该端口 |
| `[::1]:5173` | 带端口的 IPv6 地址 |

主机名会转成小写、做 IDNA 编码，并去掉末尾的点后再比较。不允许在 IP 地址上使用通配符。`block_hosts` 中的 IP 地址，也会拦截解析到该地址的主机名。

即使写进了 `allow_hosts`，链路本地地址也照样被拒绝；没有任何 option 能放行它们。

## 与 sandbox 一起使用

两者都启用时，浏览器会遵从 [`sandbox`](../reference/builtin-plugins.md#sandbox) 插件。代理对每个连接都会询问 sandbox，所以 sandbox 的 options 改了之后，从下一个请求开始生效。sandbox 被打开或关闭，或者它禁止网络时，浏览器已打开的连接会被断开。

- **`network: deny`**：任何浏览器工具都不会运行，sandbox 在调用到达浏览器之前就会拒绝。
- **`network_allowlist`**：`browser_navigate` 和 `browser_tabs` 的 URL 参数要过它的检查，代理打开的每一个连接（包括子资源）也一样。allowlist 只写主机名（不带端口），它叠加在浏览器自己的 `allow_hosts` 之上，一个主机必须两边都通过。
- **`os_enforce: require`**：浏览器拒绝启动（已在运行的也会被关闭），因为 Chromium 无法在 sandbox 的子进程隔离下运行。浏览器被视为网络工具，所以 `syscall` 级别不会拦它。
- **文件路径**：`browser_act` 的 `files`（读取）和 `browser_screenshot` 的 `path`（写入）与其他文件工具一样经过路径检查。
- **`backend: audit`** 只记录本来会拒绝的操作，然后放行。

## 与 permgate 一起使用

[`permgate`](../reference/builtin-plugins.md#permgate) 不需要任何针对浏览器的设置。把会离开当前页面、修改页面或在页面中运行脚本的工具设为需要批准，只读的工具放行：

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

`browser_snapshot` 和 `browser_screenshot` 只读取页面，不必把关。`gated_tools` 为空表示所有工具都要批准，所以要把它们明确列出来。

## 密钥

options 里只放引用，从不放值：

```yaml
plugins:
  - name: browser
    options:
      secrets:
        staging_pw: "env:STAGING_PASSWORD"
```

一个写了 `secret: staging_pw`（代替 `text`）的 `type` 动作，会输入 `STAGING_PASSWORD` 的值；这个值在动作执行时才从环境变量读取，至少要有四个字符。在所有文本结果中，它（包括原文以及 URL 编码、JSON 编码后的写法）都显示为 `[secret:staging_pw]`。密码框也会被遮蔽：在快照里，密码框的值显示为 `(redacted)`（如果是这个密钥填进去的，则显示 `[secret:staging_pw]`）；同一个值出现在结果的其他地方（标题、URL）时显示为 `[redacted]`。

仍然可能看到密钥的地方：

- **截图。** 如果页面把这个值作为可见文字显示出来，它就会出现在图片里。
- **`browser_eval`。** 脚本拥有页面的权限，可以读取输入框的值。涉及密钥时，请用 `permgate` 给 `browser_eval` 把关。
- **网站本身**，这是当然的。只把密钥输入到你信任的网站。

## 不可信的页面内容

页面自行决定的一切内容（文字、标题、元素名称、对话框消息、文件名、脚本结果、来自页面的错误文字）都包在一个 `page_content` 元素里交付，它带有一个在整个会话内固定的随机 id 和 `trust="untrusted"`；内容来自与结果第一行 URL 不同的 origin 时（点击打开的标签页、标签页列表），还会带上 `origin` 属性。内容里任何看起来像结束标签的东西都会被转义，所以页面无法提前关闭这个包装。插件还会在 system prompt 中加入一小段 `## Browser pages`，说明这些内容是数据，绝不是指令。

快照也会丢掉人看不到的文字：标记为 `aria-hidden` 的元素，以及透明、极小、与背景同色、远在屏幕之外或被裁掉的非交互文字。结果会说明丢掉了多少块。可交互元素一律保留，因为真实控件上给读屏软件用的标签是正当的。`browser_eval` 读取的是原始页面，所以脚本仍能看到隐藏文字；它的输出同样会被包装。

这是**纵深防御，而非保证**。把内容标记为不可信能降低提示注入的成功率，但挡不住它；页面仍然可能说服模型。请限制浏览型 Creature 能访问和能做的事：`allow_hosts` 收紧，用 `permgate` 给修改类工具把关，不要给它用不上的密钥或工具。

## 多用户主机

在启用了用户账户的服务器上，只有管理员的引擎可以使用主机资源。对其他所有用户（以及匿名槽位），浏览器只能以 headless 方式、用一次性 profile、自带的 Chromium 访问公网地址。如果 Creature 的 options 要求更多（`headless: false`、`profile_dir`、`executable_path`、`channel`、`allow_loopback` 或 `allow_private`），每次浏览器调用都会报错，并列出被拒绝的 options。单用户主机、`kt run` 和 CLI 不受此限制。

## 限制与已知缺口

- 每次 `browser_act` 最多 20 个动作；`wait` 最多 10 秒。
- 最多 20 个打开的标签页；页面超出此数量打开的标签页会被关闭并报告。
- `browser_eval` 的结果在 10000 字符处截断，脚本必须在 `action_timeout_s` 内完成。
- 截图为 PNG；当 PNG 会超过约 500 KB 时改用 JPEG。整页截图最高 8000 像素。
- 每个动作之后，会话会等待页面安静下来（150 毫秒内没有 DOM 变化，最多等 2 秒）。它看不到尚未触发的定时器：页面稍后才更新时，加一个 `wait`，等你期待的文字出现。
- **只有原生工具调用才能把截图送到模型。** 在文本工具格式（`bracket`、`xml`）下，工具结果以文本形式传回，所以模型只拿到截图的文字行（截了什么、存在哪里），看不到图片。需要截图时请使用 `tool_format: native`，或者用 `path` 把截图存下来。
- 每个 Creature 一个浏览器；它上面的调用一次执行一个。
- 只支持经由 Playwright 驱动的 Chromium。
- 不支持：连接到你自己正在运行的 Chrome 或其默认 profile；隐身、反机器人规避或破解验证码（遇到验证码或两步验证提示，应交给用户处理）；控制桌面或其他应用程序。
- 尚未实现：专门的浏览子代理、提示注入分类器、提供商原生的 computer-use 工具，以及对既往浏览流程的记忆。

## 示例

[`examples/agent-apps/web_tester`](../../../examples/agent-apps/web_tester/) 是一个检查本机上运行的网页应用的小型 Creature。它启用浏览器时打开了 `allow_loopback`，并把 `allow_hosts` 限定为 `localhost` 和 `127.0.0.1`；使用原生工具调用，让截图能送到模型；它的 prompt 要求只报告页面上确实显示出来的结果。

```bash
kt run examples/agent-apps/web_tester --llm <你的预设>
```

## 疑难排解

- **"the browser tools need Playwright, which is not installed"。** 安装 extra：`pip install "kohakuterrarium[browser-use]"`，然后运行 `playwright install chromium`。
- **"no usable Chromium was found"。** 运行 `playwright install chromium`，或者用 `executable_path` 或 `channel` 指向一个已安装的 Chromium 系浏览器。
- **"the browser needs the Proactor event loop on Windows"。** Playwright 以子进程方式启动它的驱动，而只有 Proactor 事件循环支持子进程。进程里有东西换成了 selector 循环：最常见的是带 `--reload` 启动的服务器（在 Windows 上，uvicorn 会让重载后的应用跑在 selector 循环上），或者设置了 `WindowsSelectorEventLoopPolicy` 的代码。去掉 `--reload` 启动服务器，或移除那个 policy。
- **导航被拦截。** 报错会写明 URL 和原因。本地服务器请设 `allow_loopback: true`（局域网里的机器再加 `allow_private: true`）；主机不在 `allow_hosts` 里就把它加进去；也检查一下 sandbox 的 `network_allowlist`。链路本地地址无法放行。
- **每次调用都是 "dialog pending"。** 有一个 `confirm` 或 `prompt` 对话框开着。在 `browser_act` 里用 `dialog` 动作回答它（`accept` 为 true 或 false，prompt 用 `text`）。
- **"ref eN is no longer on the page" / "now points at a different element"。** 页面在模型上次看过之后变了。重新快照，用新的 ref。
- **"the browser tools need the browser plugin"。** 有某个 `browser_*` 工具被写在 `tools:` 下，却没有启用插件。改为启用 `browser` 插件，它会带上全部六个工具。
- **"the browser session is closed"。** Creature 已停止，或插件被停用。重新启用插件，下一次调用会启动一个全新的浏览器。

## 延伸阅读

- [参考 / 内建插件](../reference/builtin-plugins.md#browser)：option 表。
- [参考 / 内建组件](../reference/builtins.md#browser)：六个工具。
- [插件](plugins.md)：插件如何挂接到 agent，包括 `sandbox` 与 `permgate`。
- [配置](configuration.md)：`plugins:` 在 Creature 配置中的位置。
