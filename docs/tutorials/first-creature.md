# First Creature

**Problem:** you have KohakuTerrarium installed and you want to go from
zero to a customised, runnable creature that you understand.

**End state:** you have run an out-of-the-box creature, resumed a
session, forked the creature into your own folder, changed its system
prompt, added one tool, and run it again.

**Prerequisites:** `kt` on your `PATH` (`uv pip install -e .` from the
repo, or a released install) and an API-capable machine.

A creature is a standalone agent — controller + input + output + tools
(+ optional triggers, sub-agents, plugins). This tutorial walks the
shortest path that touches all the relevant moving parts.

## Step 1 — Install the default package

Goal: get the shipped creatures (swe, general, reviewer, root, …) onto
your machine so you can reference them with `@kt-defaults/...`.

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
```

`kt install` takes a git URL or a local path. After this finishes, the
package lives at `~/.kohakuterrarium/packages/kt-defaults/` and any
config can reference it via `@kt-defaults/...`.

Verify:

```bash
kt list
```

You should see `kt-defaults` and the creatures it contains (`swe`,
`general`, `reviewer`, `root`, `researcher`, `ops`, `creative`).

## Step 2 — Authenticate to an LLM

Goal: pick one provider and log in. The SWE creature uses the default
model; you need a credential for it.

If you have a ChatGPT subscription and want OAuth:

```bash
kt login codex
```

Otherwise, set a key for any other backend (OpenAI, Anthropic,
OpenRouter, …) via:

```bash
kt config key set openai
```

You can also set a default model preset so you do not need `--llm` on
every command:

```bash
kt model list
kt model default gpt-5.4
```

## Step 3 — Run an out-of-the-box creature

Goal: see a complete creature work before you change anything.

```bash
kt run @kt-defaults/creatures/swe --mode cli
```

Ask it something simple:

```text
> list the python files in this directory
```

It should stream the answer, call tools (`glob`, `read`), and show the
output. Exit with `/exit` or Ctrl+C. On exit, `kt` prints a resume hint
that looks like `kt resume <session-name>`; sessions auto-save to
`~/.kohakuterrarium/sessions/*.kohakutr`.

## Step 4 — Resume the session

Goal: confirm that sessions are persistent and resumable.

```bash
kt resume --last
```

This picks up the most recent session. You are back in the same
conversation with the same scratchpad, tool history, and model. Exit
again when you are done.

## Step 5 — Fork the creature into a local folder

Goal: have a creature you own, layered on top of the SWE one.

```bash
mkdir -p creatures/my-swe/prompts
```

`creatures/my-swe/config.yaml`:

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-defaults/creatures/swe"

system_prompt_file: prompts/system.md
```

`creatures/my-swe/prompts/system.md`:

```markdown
# My SWE

You are a careful repo-surgery agent.

House rules:
- read before editing, always
- keep diffs small and obvious
- when unsure, ask rather than guess
```

`base_config` pulls in everything from the SWE creature — LLM defaults,
tool set, sub-agents, the upstream system prompt. Your `system.md` is
appended to the inherited prompt (prompts concatenate along the
inheritance chain). Everything else you did not set stays inherited.

## Step 6 — Add one tool

Goal: extend the inherited tool list by one entry. Web search is a
useful one.

Edit `creatures/my-swe/config.yaml`:

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-defaults/creatures/swe"

system_prompt_file: prompts/system.md

tools:
  - { name: web_search, type: builtin }
```

Lists like `tools:` and `subagents:` **extend** the inherited list
(deduplicated by `name`) unless you opt out via `no_inherit:`. So this
adds `web_search` to the SWE tool set without re-declaring the other
entries.

## Step 7 — Run your creature

```bash
kt run creatures/my-swe --mode cli
```

Ask it something that needs the web:

```text
> search the web for "kohakuterrarium github" and summarise the top result
```

You should see the house rules from your system prompt take effect,
and the new `web_search` tool become available. Exit cleanly; the
session saves automatically.

## What you learned

- A creature is a **folder with a config**, not a prompt.
- `kt install` + `kt login` + `kt run` is the whole OOTB flow.
- `kt resume` brings back a full session from disk.
- `base_config: "@pkg/creatures/<name>"` inherits everything; scalars
  override, `tools:` / `subagents:` extend.
- `system_prompt_file` concatenates along the inheritance chain.

## What to read next

- [Creatures](../guides/creatures.md) — every config field, in context.
- [Configuration reference](../guides/configuration.md) — the exact
  schema and inheritance rules.
- [First custom tool](first-custom-tool.md) — when `builtin` is not
  enough.
- [What is an agent](../concepts/foundations/what-is-an-agent.md) — the
  mental model that makes the config shape make sense.
