# Getting Started

For readers who have never run KohakuTerrarium before and want a working agent on their machine in a few minutes.

KohakuTerrarium ships a core framework plus an install path for reusable creature/plugin packs. The official pack, `kt-defaults`, gives you a ready-to-use SWE agent, a reviewer, a researcher, and a few terrariums. You don't have to write anything to try it.

Concept primer: [what is a creature](../concepts/foundations/what-is-an-agent.md), [why this framework](../concepts/foundations/why-kohakuterrarium.md).

## 1. Install

### From PyPI (recommended)

```bash
pip install kohakuterrarium
# or, with more optional deps (speech, embeddings, etc.)
pip install "kohakuterrarium[full]"
```

This gives you the `kt` command. Verify:

```bash
kt --version
```

### From source (for development)

```bash
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"
```

If you want `kt web` or `kt app` to serve the frontend, build it once:

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

Without the build step, `kt web` prints a message and `kt app` fails to open.

## 2. Install the default creature pack

`kt-defaults` contains the OOTB creatures (`swe`, `reviewer`, `researcher`, `ops`, `creative`, `general`, `root`) and a few terrariums.

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
kt list
```

Installed packages live at `~/.kohakuterrarium/packages/<name>/` and are referenced with the `@<package>/path` syntax.

## 3. Authenticate a model provider

Pick one:

**Codex (ChatGPT subscription, no API key)**
```bash
kt login codex
kt model default gpt-5.4
```

A browser window opens; finish the device-code flow; tokens land in `~/.kohakuterrarium/codex-auth.json`.

**OpenAI-compatible provider (API key)**
```bash
kt config key set openai          # prompts for key
kt config llm add                 # interactive preset builder
kt model default <preset-name>
```

**Other providers**: `anthropic`, `openrouter`, `gemini`, etc. are built-in backends. See `kt config provider list` and [Configuration](configuration.md) for details.

## 4. Run a creature

```bash
kt run @kt-defaults/creatures/swe --mode cli
```

You land in an interactive prompt with the SWE agent. Type a request; it uses shell, file, and editing tools in the current working directory. Ctrl+C exits cleanly and prints a resume hint.

Modes:

- `cli` — Rich inline (default on TTY)
- `tui` — Full-screen Textual app
- `plain` — Bare stdout/stdin, for piping or CI

Override the model for one run:

```bash
kt run @kt-defaults/creatures/swe --llm claude-opus-4.6
```

## 5. Resume

Sessions auto-save to `~/.kohakuterrarium/sessions/*.kohakutr` (unless you pass `--no-session`). Restart any past session:

```bash
kt resume --last                # most recent
kt resume                       # interactive picker
kt resume swe_20240101_1234     # by name prefix
```

The agent rebuilds from the saved config, replays conversation, re-registers resumable triggers, and restores scratchpad and channel history. See [Sessions](sessions.md) for the full persistence model.

## 6. Search session history (hint)

Because sessions are stored operationally, you can search them like a small local knowledge base:

```bash
kt embedding ~/.kohakuterrarium/sessions/<name>.kohakutr
kt search <name> "auth bug"
```

Full walk-through: [Memory](memory.md).

## 7. Open the web UI or desktop app

```bash
kt web           # local web server at http://127.0.0.1:8001
kt app           # native desktop window (requires pywebview)
```

For a daemon that outlives your terminal:

```bash
kt serve start
kt serve status
kt serve logs --follow
kt serve stop
```

See [Serving](serving.md) for when each surface is appropriate.

## Troubleshooting

- **`kt login codex` doesn't open a browser.** Copy the URL the CLI prints and paste it into a browser manually. If the callback port is busy, free it before re-running.
- **`kt web` serves nothing / 404s on `/`.** The frontend isn't built. Run `npm install --prefix src/kohakuterrarium-frontend && npm run build --prefix src/kohakuterrarium-frontend`. PyPI installs ship the built assets already.
- **`Permission denied` writing to `~/.kohakuterrarium/`.** The framework creates that directory on first run. If it already exists but is owned by another user (common after `sudo pip install`), fix ownership: `chown -R $USER ~/.kohakuterrarium`.
- **`kt run` says "no model set".** You skipped step 3. Run `kt model default <name>` or pass `--llm <name>`.
- **`ModuleNotFoundError: pywebview`.** `kt app` needs the desktop extra: `pip install 'kohakuterrarium[full]'` (or use `kt web`).

## See also

- [Creatures](creatures.md) for how to inherit from or customize the OOTB agents.
- [Sessions](sessions.md) for resume semantics and compaction.
- [Serving](serving.md) to decide between `kt web`, `kt app`, and `kt serve`.
- [Reference / CLI](../reference/cli.md) for every command and flag.
