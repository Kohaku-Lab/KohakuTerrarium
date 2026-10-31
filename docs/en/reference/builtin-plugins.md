---
title: Built-in plugins
summary: Reference for the runtime plugins shipped with the framework. Sandbox, budget, permgate, compact.auto, ui.apps.
tags:
  - reference
  - plugins
  - builtin
---

# Built-in plugins

These runtime plugins ship with KohakuTerrarium. None of them are
privileged in the framework; they use the same hooks user plugins
do. They are auto-discovered at agent start; configs activate them
under `plugins:` like any other plugin.

For the framework / plugin boundary lesson and a worked walkthrough
of the sandbox plugin, see
[the worked example in guides/plugins](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature).

| Name | Priority | Hooks | Implements |
|---|---|---|---|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`, `runtime_services`, `on_load` | Filesystem / network / subprocess capability gating. |
| [`budget`](#budget) | 5 | `pre_llm_call`, `post_llm_call`, `pre_tool_execute`, `pre_subagent_run`, `get_prompt_content` | Turn / tool-call / walltime budget accounting. |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`, `on_load` | Trigger eager context compaction after high-token LLM calls. |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`, `on_load` | Interactive user approval for tool calls. |
| [`ui.apps`](#uiapps) | 50 | `contribute_tools`, `presentation_contribution` | Adds `show_app`: model-written HTML apps in the web UI. |
| [`ui.declarative`](#uideclarative) | 50 | `presentation_contribution`, `pre_llm_call` | Inline UI: `<ui>` components in replies (renderer `kt.ui`). |

Lower priority runs earlier on `pre_*` hooks. The order above is the
default registration order.

`ui.apps` and `ui.declarative` are **on by default**; every other plugin
starts off. Both add nothing until an attached client draws their renderer, so
a headless creature gets no prompt or tool from them. Turn one off with an
entry in `plugins:`:

```yaml
plugins:
  - name: ui.apps
    enabled: false
```

A plugin that is on only by default yields: if one of its tools has a name a
configured tool or another plugin already uses, it is turned off with a
warning instead of failing the build. A user's own toggle in the Plugins tab is
saved with the session and wins on resume. KohakuTerrarium's own MCP tool
server, which has no chat client, starts both off.

## `sandbox`

Hard capability gate. Restricts what tools and subprocesses can do
without modifying the tools themselves. Implementation:
`src/kohakuterrarium/builtins/plugins/sandbox/plugin.py`. Profile
presets: `src/kohakuterrarium/modules/sandbox/profile.py`.

### Options

| Option | Type | Values | Default | Purpose |
|---|---|---|---|---|
| `enabled` | bool | true / false | true | Master on / off. |
| `backend` | enum | `auto` / `audit` / `off` | `auto` | `auto` = block on violation; `audit` = log only; `off` = no enforcement. |
| `profile` | enum | `PURE` / `READ_ONLY` / `WORKSPACE` / `NETWORK` / `SHELL` | `WORKSPACE` | Capability preset. |
| `fs_read` | enum | `default` / `deny` / `workspace` / `broad` | from profile | File-read scope override. |
| `fs_write` | enum | `default` / `deny` / `workspace` / `broad` | from profile | File-write scope override. |
| `network` | enum | `default` / `deny` / `allow` | from profile | Network access override. |
| `syscall` | enum | `default` / `pure` / `fs` / `shell` / `any` | from profile | Subprocess capability level. |
| `env` | enum | `default` / `filtered` / `inherit` | from profile | Subprocess environment handling. |
| `tmp` | enum | `default` / `private` / `shared` | from profile | Temp directory isolation. |
| `fs_deny` | list[str] | paths | `[]` | Additional path deny-list (env-var expansion supported). |
| `network_allowlist` | list[str] | hostnames | `[]` | Allowed hosts when `network=allow`. Empty list = allow all when network is allowed. |
| `os_enforce` | enum | `auto` / `require` / `off` | `auto` | Kernel write confinement for subprocesses. `auto` = confine where the OS supports it, `require` = refuse to spawn where it does not, `off` = policy checks only. |
| `fs_write_allow` | list[str] | paths | `[]` | Exceptions you name: writable even inside `fs_deny` (not under `fs_write=deny`). |
| `locked` | bool | true / false | false | Refuse runtime disable, option changes and working-directory switches (409 from the HTTP API). Can be turned on at runtime, not off; unlock by restarting the server with `KT_SANDBOX_ALLOW_UNLOCK=1`. |
| `blocked_tools` | list[str] | tool names | `[]` | Tools the agent cannot call regardless of args. |

### Profile presets

| Profile     | fs_read | fs_write   | network | syscall | env      |
|-------------|---------|------------|---------|---------|----------|
| `PURE`      | deny    | deny       | deny    | pure    | filtered |
| `READ_ONLY` | broad   | deny       | default | fs      | default  |
| `WORKSPACE` | default | workspace  | allow   | fs      | default  |
| `NETWORK`   | deny    | deny       | allow   | default | default  |
| `SHELL`     | default | workspace  | allow   | shell   | filtered |

### Behaviour

- **Path scopes**: `default` allows under `cwd`; `workspace` allows
  under `cwd` and rejects parent traversal; `broad` allows anywhere
  except `fs_deny`; `deny` blocks all paths.
- **Network gating**: when `network=allow` and `network_allowlist`
  is set, only listed hosts pass the check; otherwise all hosts pass.
  When `network=deny`, all network tool calls (`web_fetch`,
  `web_search`) raise `PluginBlockError`.
- **Subprocess gating**: the plugin publishes a `subprocess_runner`
  service via `runtime_services()`. Tools that need to spawn
  subprocesses (`bash`, etc.) consume it from
  `ToolContext.runtime_services`. The runner checks syscall level
  (`pure` blocks all spawns, `fs` blocks network calls, `shell`
  allows everything) and the network allowlist before delegating to
  `asyncio.create_subprocess_exec`.
- **Kernel write confinement** (macOS): the runner launches each command
  under `sandbox-exec` with a profile built from `fs_write`, so the command
  and every process it starts are confined, whatever they do. A script that
  calls `shutil.rmtree(os.environ["HOME"])` fails with `Operation not
  permitted`. `workspace` allows writes under the working directory, the
  system temp directories, the user caches (`~/.cache`, `~/Library/Caches`,
  `~/.npm`) and `/dev`; `deny` allows only `/dev`; `broad` allows
  everything. `fs_deny` paths are then unwritable, and so are the directory
  entries above them, so a denied path cannot be moved away by renaming a
  parent. `fs_write_allow` entries are writable again, so a narrow exception
  such as `~/.ssh/sockets` (ssh connection sharing) or `~/.config/gcloud`
  survives a denied parent. Launchers that would run work outside the
  sandbox (`open`, `osascript`, `launchctl` and the LaunchServices / Apple
  Events services) are refused, and so are connections to the control
  sockets of Docker (Desktop, Colima, OrbStack), Podman, GNU screen, tmux
  (including `TMUX_TMPDIR`) and iTerm2, which would run commands unconfined.
  This is a denylist: other local unix sockets stay reachable, because a
  default-deny would also cut DNS (`mDNSResponder`) and `ssh-agent`. Reads and network are not confined by the kernel: secrets
  stay readable, so pair the sandbox with `env=filtered` where API keys
  should not be inherited. `write` / `edit` checks under `fs_write=workspace` admit the same
  roots. The confinement targets accidental damage (a misdirected `rmtree`,
  a stray `open`); it is not a boundary against a program that deliberately
  escapes, for example by submitting a launchd job through a copied
  launcher or logging in again over `ssh localhost`. Work started that way
  runs unconfined; run untrusted code as a separate macOS user or in a VM.
  The shared `/tmp` stays writable, so a creature can still damage another
  process's scratch files there, and a hard link made inside the workspace
  can be written through. Since an agent can reach the local API, set
  `locked: true` so it cannot switch the sandbox off.
  Commands run in their own process group, and a timeout or cancellation
  kills the whole tree. Other platforms run unconfined under `auto`; use `require` to refuse
  instead.
- **`backend=audit`**: violations are logged via the agent logger
  rather than raised. Useful for first-pass discovery on a new
  workload.
- **Hot reconfigure**: calling the plugin's `refresh_options()`
  rebuilds the internal capability struct; subsequent
  `pre_tool_execute` calls see the new policy without restart.

### Example configurations

```yaml
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com", "github.com"]
      fs_deny: ["~/.ssh", "$HOME/.aws"]
```

```yaml
# Audit-only mode for safe rollout
plugins:
  - name: sandbox
    options:
      profile: SHELL
      backend: audit
```

```yaml
# Pure compute, no I/O at all
plugins:
  - name: sandbox
    options:
      profile: PURE
      blocked_tools: ["web_fetch", "web_search", "bash"]
```

For `glob`, the read policy checks the actual `path` (default `.`) using the
tool's path resolution, including local file URIs. Literal pattern prefixes
are resolved relative to that base. Enforcing mode rejects absolute patterns
and parent traversal after wildcard segments; use an explicit allowed `path`
instead. Audit mode logs violations and off mode bypasses these checks.
These argument checks do not provide recursive symlink or OS process isolation.

## `budget`

Multi-axis budget accounting and enforcement.
Implementation: `src/kohakuterrarium/builtins/plugins/budget/plugin.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `turn_budget` | `[soft, hard]` ints or null | null | Cap on LLM turns. Soft = warn; hard = block. |
| `tool_call_budget` | `[soft, hard]` ints or null | null | Cap on total tool calls. |
| `walltime_budget` | `[soft, hard]` seconds or null | null | Cap on wall-clock seconds. |
| `subagent_turn_budget` | `[soft, hard]` ints or null | null | Per-sub-agent run turn cap. |
| `inject_alert` | bool | true | Inject a soft-limit alert into the next system / user message when the soft threshold is crossed. |

When the hard threshold is crossed on any axis, the next
`pre_tool_execute` / `pre_llm_call` / `pre_subagent_run` raises
`PluginBlockError` with a message naming the axis. When the soft
threshold is crossed, an alert is injected so the LLM can wind the
work down voluntarily before the hard cap fires.

### State

Usage belongs to the live plugin instance. Updating limits preserves
already-accounted usage and recomputes alarms for changed axes; unchanged
axes retain their pending alarms. Disabling and re-enabling an axis retains
its previous count, but work performed while that axis is disabled is not
counted. These counters are not persisted across process/session resume.

### Example

```yaml
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## `compact.auto`

Trigger eager background context compaction when LLM token usage crosses the
threshold configured by the host compact manager.
Implementation: `src/kohakuterrarium/builtins/plugins/compact/auto.py`.

### Configuration

This plugin has no plugin-specific options. Configure its host manager through
the [`compact:` block](configuration.md#compact); see the
[sub-agent guide](../guides/sub-agents.md#auto-compaction-for-sub-agents) for the inline
sub-agent form.

### Behaviour

After every LLM call, `post_llm_call` checks token usage against the
configured threshold. If exceeded, it calls
`context.compact_manager.trigger_compact()` which schedules
compaction asynchronously: the controller keeps running while the
summariser works, and the swap happens atomically between turns.
See [non-blocking compaction](../concepts/impl-notes/non-blocking-compaction.md).

On a main agent, disabling `compact.auto` removes this eager hook but retains
the core deferred check after tool feedback and at turn end. Sub-agents have
no separate deferred host-loop check, so they require `compact.auto` for
automatic compaction. In both cases, `compact.enabled: false` disables
automatic triggers; an explicit `/compact` command remains available on main
agents.

### Pack alias

`auto-compact` is a built-in pack that expands to a single
`compact.auto` activation with default options. Configs that just
want the feature on can use `default_plugins: ["auto-compact"]`.

## `permgate`

Interactive user approval for tool calls. The agent emits a
confirmation event on the output bus, waits for the user's reply,
and either proceeds or aborts based on the answer.
Implementation: `src/kohakuterrarium/builtins/plugins/permgate/plugin.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `enabled` | bool | true | Master on / off. |
| `tools` | list[str] / `"all"` | `"all"` | Which tools require approval. `"all"` = every tool. |
| `whitelist` | list[str] | `[]` | Tools that bypass approval (added to a per-session "always allow" list when the user picks "always"). |
| `auto_approve_pattern` | str | "" | Regex pattern; matching tool args auto-approve without prompting. Use sparingly. |
| `prompt_template` | str | default | Customise the approval prompt shown to the user. |

### Behaviour

`pre_tool_execute` emits a `tool_approval_request` output event with
the tool name, args, and a request id. The agent waits on the output
bus for a `tool_approval_reply` event with a matching id; the reply
carries `decision ∈ {approve, deny, always}`. `always` adds the tool
to a per-session whitelist persisted in plugin state. `deny` raises
`PluginBlockError` so the agent sees the rejection as a tool failure.

The web UI and TUI render the approval as an inline prompt on the
output stream; CLI surfaces it via the same output bus and replies
through the input loop. Headless deployments without a user surface
should disable permgate or set `auto_approve_pattern: "^.*$"`.

### Example

```yaml
plugins:
  - name: permgate
    options:
      tools: ["bash", "write", "edit"]
      whitelist: ["read", "grep", "glob"]
```

## `ui.apps`

Contributes the `show_app` tool: the agent shows one HTML document as an
interactive app in the web UI canvas or chat. It is the full-page option for
when inline UI is not enough. The tool, its guidance and its part of
`## Presenting your answer` are visible to the model only while an attached
client renders `kt.app`. Implementation: `src/kohakuterrarium/builtins/plugins/apps/plugin.py`
and `src/kohakuterrarium/builtins/tools/app/show.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `allowed_hosts` | str / list[str] | `common` | Hosts the app may load scripts, styles, fonts and images from: preset names (`common`, `fonts`, `tailwind`) and bare host names. `[]` = no network. `connect-src` is always `'none'`. |
| `default_surface` | `canvas` / `chat` | `canvas` | Where a new app opens. |
| `max_document_kb` | int | 256 | Largest app document. |

### Example

```yaml
plugins:
  - name: ui.apps
    options:
      allowed_hosts: [fonts, cdn.jsdelivr.net]
```

See [Chat widgets](../guides/chat-widgets.md) for the bridge, the sandbox
and compaction behaviour.

## `ui.declarative`

Inline UI: the model writes `kt.ui` components (tables, charts, diagrams,
stats, tabs, inputs, buttons) as `<ui>` blocks inside its normal replies.
Implementation: `src/kohakuterrarium/builtins/plugins/ui_declarative/` and
`src/kohakuterrarium/builtins/kt_ui/markup.py`.

### Behaviour

- While an attached client reports `kt.ui` (the web UI after its
  `client_hello`, the Rich CLI and the TUI), the plugin adds the
  inline-components part of `## Presenting your answer`: when to use each
  component, the markup, the props of the common components and one
  example. The less common components (`accordion`, `timeline`, `tree`,
  `gallery`, `quiz`, `timer`, `html` …) are named in one line;
  `info("ui:<name>")` returns their props and an example. When the model
  cannot call `info`, every component is written out. Headless creatures
  never get the part.
- A block that fails to convert or validate is drawn as an error box; the
  next model request carries one note naming the block and its errors, with
  the props (and an example) of each component the errors name.
- The VS Code view cannot run app frames inside its chat, so a `show_app`
  app or an inline `html` block opens in its own editor tab, behind a fixed
  policy with no network connections. The app's bridge is relayed through
  the extension, and each `kt.send` asks the user first ("Send" or "Always
  for this app"), because every script in that tab is the model's.
- A button or form `send` posts its text as the user's next message.

The plugin has no options (an old `default_surface` option is accepted and
ignored). See [guides/chat-widgets](../guides/chat-widgets.md#inline-ui).

### Example

```yaml
plugins:
  - name: ui.declarative
```

## Activation summary

```yaml
# Maximally locked-down agent with a permission gate, budget, and
# auto-compaction.
default_plugins: ["auto-compact"]
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com"]
  - name: permgate
    options:
      tools: "all"
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## See also

- [guides/plugins](../guides/plugins.md): how to write your own.
- [reference/plugin-hooks](plugin-hooks.md): every hook signature.
- [concepts/modules/plugin](../concepts/modules/plugin.md): design
  rationale.
