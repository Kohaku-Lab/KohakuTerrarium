# Output

## What it is

The **output** module is how a creature talks back to its world. It
receives everything the controller emits — text chunks from the LLM,
tool start/complete events, activity notifications, token-usage
updates — and routes each one to the right sink.

There can be more than one sink. A creature can print to stdout, stream
to TTS, push to Discord, and log to a file, all at once.

## Why it exists

"Print the LLM's reply to stdout" is the trivial case. Real deployments
have to answer questions the trivial case does not:

- Where does a streamed LLM chunk go when there are three listeners?
- What about tool activity — the same stream, or a different one?
- Should user-facing text and log-facing text share a sink?
- If the creature runs under the web UI, who is subscribing to events?

Rather than special-case each surface, the framework has a single
router that treats every sink as a named output.

## How we define it

An `OutputModule` is an async consumer with methods like
`on_text(chunk)`, `on_tool_start(...)`, `on_tool_complete(...)`,
`on_resume(events)`, `start()`, `stop()`. The `OutputRouter` owns a set
of them — a default and any number of `named_outputs` — and fans
events out.

`controller_direct: true` (the default) means the controller's text
stream flows directly to the default output. `controller_direct: false`
lets you interpose a processor (rewriter, safety filter, summariser).

## How we implement it

Built-in outputs:

- **`stdout`** — plain terminal, prefix/suffix/stream-suffix configurable.
- **`tts`** — text-to-speech; backends include Fish, Edge, OpenAI,
  auto-selected at runtime.
- **`tui`** — Textual-based display when the creature runs under a TUI.
- **(implicit) web streaming output** — used when the creature runs
  inside the HTTP/WebSocket server.

`OutputRouter` (`modules/output/router.py`) also exposes an activity
stream used by the TUI and HTTP clients to show tool start/complete
events without routing them through the text channel.

## What you can therefore do

- **Silent controller, streaming sub-agent.** Mark a sub-agent with
  `output_to: external` — its text streams to the user while the
  parent controller stays internal. The user sees a coherent reply
  that was composed by a specialist.
- **Per-purpose sinks.** Route user-visible answers to stdout, route
  debug notes to a `logs` named output that writes to a file, route
  final artifacts to a Discord webhook.
- **Post-process text.** Set `controller_direct: false` and add a
  custom output that cleans, translates, or watermarks the controller's
  text before it reaches the user.
- **Transport-independent code.** The same creature runs on CLI, web,
  or desktop because the output layer abstracts the transport.

## Don't be bounded

A creature without output is legitimate: some triggers only cause
side effects (write a file, send an email). Conversely, outputs are
full modules — a Python module can decide to run a mini-agent that
chooses how to format each chunk. That sounds excessive and mostly
is, but it is an option.

## See also

- [Sub-agent](sub-agent.md) — `output_to: external` streams directly through the router.
- [Controller](controller.md) — what actually feeds the router.
- [reference/builtins.md — Outputs](../../reference/builtins.md) — built-in list.
- [guides/custom-modules.md](../../guides/custom-modules.md) — writing your own.
