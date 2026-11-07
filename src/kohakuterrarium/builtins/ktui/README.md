# builtins/ktui/

KohakuTerrarium's terminal front end on [kohakutui](../../../../../KohakuTUI): one shell for
every creature of a running engine, rendered inline (scrollback), hybrid or full screen. Run it with `kt run <creature> --mode ktui` or
`kt resume --mode ktui`; it will replace `kt-cli` / `kt-tui` once it reaches parity.

## Files

| File | Description |
| --- | --- |
| `shell.py` | `KtuiShell`: the app, composer, active-items row, footer; input, queue, `@name`, focus |
| `commands.py` | Slash commands: ktui's own and the focused creature's, the `/` popup list, running one |
| `board.py` | `Board`: the list behind the active-items row (creatures, sub-agents, jobs, approvals) and the live sub-agent trace |
| `settings.py` | `Settings`: `/config` (display, models, keys, providers, MCP) and the `/model`, `/theme`, `/renderer` commands |
| `trace.py` | `Traces`: every sub-agent's task, tool calls and result, recorded focused or not; the trace view |
| `runs.py` | `ToolRuns`: consecutive tool calls grouped into batch cells; tool output in scroll boxes |
| `models.py` | `/model` picker items from KT presets, answers back to selectors |
| `output.py` | `KtuiOutput`: a creature's default output; maps every KT output event to the view or the shell; reports the `kt.ui` renderer (not `kt.app`); `on_widget` takes kt.ui widgets and turns the legacy kinds back into their events |
| `view.py` | `CreatureView`: one creature's document (a kohakutui document of its own), drawn focused or not; only the focused creature uses the live region |
| `chrome.py` | `Chrome`: full screen after the web's v2 session view: the page tabs on top (Chat, Graph, Status, Debug, Settings), the conversation rail (agents, channels) and the side panel on the Chat page; panel sizes saved in `ktui.json`; alt+1…5, alt+r / alt+s, ctrl+pgup / ctrl+pgdn |
| `chrome_data.py` | What the chrome shows, read from the shell: jobs, sub-agents, the graph, members, scratchpad, widgets, status rows, drives (read in the background) |
| `side.py` | `Side`: the Chat page's right panel, only the sections with content (members of the open channel, jobs, sub-agents, drives, canvas, widgets, scratchpad); `Sections`, the titled lists it and the pages draw with |
| `pages.py` | `Pages`: Graph, Status, Debug and Settings, each a document shown where the chat is |
| `page_status.py` | The Status page: Session, Usage, context bars, Health; Agents, Channels, Drives and Jobs with their actions |
| `page_debug.py` | The Debug page: Logs (and an attached local server's log), Events, Prompt and Trace views |
| `page_modules.py` | The Settings page's Modules: plugins and tools, switch on / off, options in a form (local or in a server) |
| `page_settings.py` | The Settings page's session sections: model per creature, modules, workspace, triggers, cost, extensions, env |
| `graph_edit.py` | Graph edits: add a creature or channel, connect, disconnect, remove |
| `side_detail.py` | Detail views in the side panel: a sub-agent's live trace, a job, a drive and its actions |
| `forms.py` | `Forms`: several fields at once (a page in full screen, one prompt at a time inline) |
| `config_edit.py` | Providers, models, MCP servers and goals: add, edit, delete; `/login` |
| `sessions.py` | `/resume` picker, `/rename`, `/export` |
| `messages.py` | Message actions: copy, edit, retry, branch versions; loading older turns |
| `attach.py` | `ComposerHelper`: `@` completion, file and image attachments, the external editor |
| `shell_input.py` | `ShellInput`: submitting, queueing, `!` shell commands, interrupts |
| `shell_extras.py` | Key help and `Attention` (terminal title, notification and bell while the window is away) |
| `tool_bodies.py` | File edits as diffs, images in tool results |
| `remote.py` | `RemoteSession`: a session in a KT server over REST and the chat socket; frames into `KtuiOutput`; reconnect and redraw |
| `remote_drives.py` | Drives of a session in a server through its drive routes, shaped like the local drive service; `drive_service` picks one |
| `remote_frames.py` | `FrameDispatch`: the chat socket's frames as output calls |
| `remote_parts.py` | Smaller stand-ins: history events, triggers, widget and `<ui>` state, a channel, modules |
| `remote_proxies.py` | Stand-ins for the engine, creatures, agents and router of a remote session |
| `servers.py` | Finding a KT server, its running sessions (and stopping one) and its log file |
| `launcher.py` | The `kt-ui` launcher: running (attach, stop), recent (resume, rename, delete) and installed (start) |
| `onboarding.py` | First run: no model yet, so sign in, add a provider or pick a model |
| `updates.py` | A newer release, said once at start (launcher-managed installs) |
| `channels.py` | `ChannelView`: a channel's conversation (history, live traffic) in a document; the composer sends to it as `human` while it is shown |
| `logtail.py` | `LogTail`: while ktui runs, `print`, stderr, Python warnings and log records (KT's from INFO, other packages' from WARNING) go to the Logs tab and `/logs` instead of over the UI |
| `view_notices.py` | `NoticeCells`: notices, errors, cards, the older-turns mark, pictures / videos / sounds (the Canvas list), compaction and progress rows |
| `view_subagents.py` | `SubagentCells`: foreground sub-agents as ◆ cells with their tool calls nested (mixed into the view) |
| `inline_ui.py` | `ReplyStream`: a streamed reply cut into Markdown and `<ui>` blocks (`kt_ui.markup_lex.split_message`); lines that may open a block are held back until decided |
| `ui_block.py` | `BlockView`: one `<ui>` block or tool widget: state, `let` values, redraws, live → frozen, where state is saved (inline state or the widget service) |
| `ui_slot.py` | `Slot`: redraws a block onto the nodes of the previous pass, so focus and scroll survive a state change |
| `ui_map.py` | kt.ui spec nodes → kohakutui widgets (`mount`); layout, text, status, lists, tabs, compare, quiz, copy, images; html uses the `kt_ui.fallback` text |
| `ui_controls.py` | Inputs (`Field`), buttons, forms and the timer: `message` is said as the user, a tool widget's `send` posts a widget message |
| `ui_charts.py` | Charts: line kinds as `Chart`, bar / stacked / radar / pie / gauge as `Bars`, heatmap as a shaded grid |
| `ui_values.py` | Prop values evaluated in a block's scope (templates, lists, numbers) |
| `tool_widgets.py` | `ToolWidgets`: kt.ui widgets tools open (`widget` / `widget_state` / `widget_close`), drawn in the live region, frozen into the transcript on close |
| `interactions.py` | `Interactions`: approvals and questions from all creatures; hide (esc / Later), alt+j, ctrl+k, replies |
| `view_state.py` | The plain state beside a creature's document: usage, jobs, messages |
| `replay.py` | Session resume through the view (live branch only) |
| `input.py` | `KtuiInput`: placeholder input so nothing else reads the terminal |
| `config.py` | `KtuiConfig`: renderer, theme, verbosity, links and the panel sizes remembered in `ktui.json` |
| `format.py` | Short display strings: tool arguments, results, durations, token counts |

The engine launcher is `terrarium/engine/cli/ktui.py`; `kt attach` is `terrarium/engine/cli/ktui_remote.py`; `kt-ui` is `cli/entry/ui.py`.

## Behaviour

- **Threads**: kohakutui renders in Rust; its events are consumed with `App.aevents()` on
  KT's asyncio loop, so outputs, input and dialogs share one loop and need no locks.
- **Busy input**: Enter while the agent works queues the message with a `pending_id`
  (shown greyed until KT claims it, ctrl+x cancels the newest); ctrl+enter or ctrl+s (for
  terminals that send ctrl+enter as a plain enter) interrupts first.
- **Several creatures**: every creature draws into its own document; the focused one is
  shown, the others count unread. `@name msg` sends one message, `/focus name`, the board, a
  tab or the rail switches; inline, the switched-to document's lines not yet in scrollback
  are written under a `── name ──` divider. Creatures that join the focus creature's graph
  while ktui runs are attached; ones removed from the engine are let go.
- **Full screen** (ctrl+t, or `renderer: fullscreen`), after the web's v2 session view:
  page tabs across the top (Chat, Graph, Status, Debug, Settings; alt+1…5). The
  Chat page has the conversation rail on the left (agents with a status mark and unread
  count, then the graph's channels: picking one shows its conversation, a channel's sends
  as `human`) and a side panel on the right with only what has content (the channel's
  members, jobs, sub-agents, drives, widgets, scratchpad). Graph, Status, Debug and Settings
  take the whole page (Settings: display choices as fields, ←/→ applies; models, keys,
  providers, MCP below). A click on a panel's title collapses it, a click on its strip opens
  it; drag a divider to resize; alt+r / alt+s toggle the rail / side panel, ctrl+pgup
  / ctrl+pgdn step through the conversations, F6 moves the keys between the chat, the tabs,
  the panels and the input (a click on the input or esc gives it the keys back). The input,
  the spinner row and notices sit in the chat column. Panel sizes are remembered.
- **Active row** (↓ from the input): one segment per kind (creatures, sub-agents, jobs,
  queued, waiting); ←/→ choose, enter opens the board on that section (an approval directly).
- **Docked dialogs**: pickers, the board, traces and approval cards open where the input box
  is, in every mode; esc gives the input back. Pickers have focus areas (sections, list,
  options): ←/→ move the focus, ↑↓ act on it. Multi-select questions tick with space and
  answer every ticked id (one id for single choice, as the web does). An approval hidden with esc (the agent keeps
  waiting) comes back with alt+j; ctrl+k approves the oldest.
- **Inline UI**: ktui reports the `kt.ui` renderer, so the `ui.declarative` plugin teaches
  the model `<ui>` blocks. A block streams in place ("building UI…" until it closes) and is
  drawn with kohakutui widgets; a block that fails to validate shows the reason. The newest
  block is live (`tab to use`): ↓↑ move between its controls, ←/→ change values, enter
  presses; its state is saved for the reply's turn and branch. The next turn (or esc)
  freezes it with its final values; full screen freezes nothing until you go back inline.
  A frozen block stays frozen (scrollback is append-only), see the KohakuTUI plan, D26.
- **Tool widgets**: a kt.ui widget a tool opens is drawn the same way in the live region;
  its state goes to the widget service and a `send` button posts a named widget message,
  so it never starts a turn. Tool updates redraw it; closing freezes it into the transcript.
  Links (markdown in replies, `<link>`, bare URLs) are kohakutui hyperlinks: ctrl+click
  inline, a plain click in full screen opens them.
- **Tool calls**: three or more in a row fold into one "N tool calls" line like the web's tool
  batch (whitespace between calls does not break a run); `verbosity: high` turns it off.
  Opened output is a scroll box: the last 12 lines, the wheel scrolls it in full screen.
- **Sub-agents**: the board lists running and the last 20 finished; enter opens the trace,
  which follows a running one (`s` stops it, `e` lists or groups its calls).
- **Keys**: esc interrupts, ctrl+c interrupts or (twice when idle) exits, ctrl+b jobs,
  ctrl+t full screen, ctrl+o expanded view (ctrl+o again goes back); in full screen ctrl+↑
  walks foldable headers (↑↓, enter toggles), clicks toggle, the pointer highlights what a
  click would open, and a drag selects text and copies it (OSC 52; shift+drag uses the
  terminal's own selection).

## Dependencies

- `kohakutui` (App, components, themes)
- `kohakuterrarium.modules.output` / `modules.input` (module contracts, `OutputEvent`, `UIReply`)
- `kohakuterrarium.terrarium.engine.cli.commands` (trusted context for `needs_engine` commands)
- `kohakuterrarium.session.history.replay` (live-branch selection for resume)
- `kohakuterrarium.llm.profiles` (presets, API keys, default model) and
  `studio.identity.mcp.servers` (MCP list) for `/model` and `/config`
