---
title: Reference
summary: Full-surface specifications — every field, command, endpoint, hook, and Python entry point.
tags:
  - reference
  - overview
---

# Reference

Reference pages are for exact lookup. Use them when you already know
what you are trying to do and need the precise command, endpoint,
class, field, or hook.

For mental models, read [Concepts](../concepts/README.md). For
task-oriented paths, read [Guides](../guides/README.md).

## Pages

- [CLI](cli.md) — every `kt` command and flag.
- [HTTP and WebSocket API](http.md) — every REST route, WebSocket
  endpoint, and Pydantic schema.
- [Python API](python.md) — every public class, function, and protocol.
- [Configuration](configuration.md) — every field of creature configs,
  terrarium configs, LLM profiles, MCP catalog, and package manifests.
- [Built-ins](builtins.md) — every shipped tool, sub-agent, input,
  output, user command, framework command, LLM provider, and LLM
  preset.
- [Built-in plugins](builtin-plugins.md) — sandbox, budget, permgate,
  compact.auto: hooks, options, behaviour.
- [Plugin hooks](plugin-hooks.md) — every lifecycle, LLM, tool,
  sub-agent, callback, and prompt hook, with signatures and timing.
- [v1.3.0 release notes](release-notes-1.3.0.md) — final changelog,
  compatibility notes, packaging flow, and validation summary.

## What belongs here

Reference docs stay narrow and exact:

- command syntax
- API endpoints
- Python classes and entry points
- config fields and interface contracts

In contrast:

- tutorials teach a path
- guides show how to accomplish a task
- concepts explain why the system is shaped the way it is
- development docs explain how to work on the framework itself
