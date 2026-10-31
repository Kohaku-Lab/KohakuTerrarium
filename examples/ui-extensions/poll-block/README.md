# poll-block

A package that pairs a backend plugin with a dashboard `message-renderer`.

- `poll_block/plugin.py` adds a `## Presenting your answer` part that teaches the
  model a fenced block named `demo.poll`. The part appears only while a client that
  announces the `demo.poll` renderer is attached (`prompt_renderers`).
- `ui/index.js` draws a closed `demo.poll` fence as buttons. Clicking one sends the
  option as the next user message (`useMessageBlock().say`).
- The stored reply keeps the fence as written, so the CLI and the session store show
  the raw JSON. An invalid payload falls back to the raw text.

The renderer id is declared in `kohaku.yaml` (`renderer: demo.poll`) and in the plugin
(`prompt_renderers`); `tests/unit/packages/slots/test_ui_examples.py` checks they agree.
