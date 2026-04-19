"""One-shot script: insert frontmatter into every markdown file in docs/.

Run from repo root:
    python scripts/add_docs_frontmatter.py
"""

import os

META = {
    "docs/README.md": (
        "KohakuTerrarium Documentation",
        "Home for the concept model, guides, reference, tutorials, and development notes.",
        ["overview", "docs"],
    ),
    # Tutorials
    "docs/tutorials/README.md": (
        "Tutorials",
        "Step-by-step walk-throughs that take you from zero to a running agent.",
        ["tutorials", "overview"],
    ),
    "docs/tutorials/first-creature.md": (
        "Your first creature",
        "Author a creature config, run it in CLI / TUI / web, and customise the prompt and tools.",
        ["tutorials", "creature", "getting-started"],
    ),
    "docs/tutorials/first-custom-tool.md": (
        "Your first custom tool",
        "Write a Python tool, register it, and wire it into a creature's config.",
        ["tutorials", "tool", "extending"],
    ),
    "docs/tutorials/first-plugin.md": (
        "Your first plugin",
        "Build a lifecycle plugin that hooks pre/post tool execution to gate or enrich calls.",
        ["tutorials", "plugin", "extending"],
    ),
    "docs/tutorials/first-terrarium.md": (
        "Your first terrarium",
        "Compose two creatures with channels and output wiring, then add a root for an interactive surface.",
        ["tutorials", "terrarium", "multi-agent"],
    ),
    "docs/tutorials/first-python-embedding.md": (
        "Embedding in Python",
        "Run an agent inside your own Python code via AgentSession and the compose algebra.",
        ["tutorials", "python", "embedding"],
    ),
    # Guides
    "docs/guides/README.md": (
        "Guides",
        "Task-oriented how-tos for authoring creatures, composing them, and shipping agents.",
        ["guides", "overview"],
    ),
    "docs/guides/getting-started.md": (
        "Getting started",
        "Install KohakuTerrarium, install the kt-biome showcase pack, and run a working agent in a few minutes.",
        ["guides", "install", "getting-started"],
    ),
    "docs/guides/configuration.md": (
        "Authoring configuration",
        "Creature config shape, inheritance, prompt chains, and the fields that matter most in day-to-day authoring.",
        ["guides", "config", "creature"],
    ),
    "docs/guides/creatures.md": (
        "Authoring creatures",
        "Prompt design, tool and sub-agent selection, LLM profile choice, and publishing creatures for reuse.",
        ["guides", "creature", "authoring"],
    ),
    "docs/guides/terrariums.md": (
        "Terrariums",
        "Horizontal multi-agent with channels, output wiring, root agents, hot-plug, and observation.",
        ["guides", "terrarium", "multi-agent"],
    ),
    "docs/guides/composition.md": (
        "Compose algebra",
        "Stitch agents and async callables together in plain Python with sequence / parallel / fallback / retry operators.",
        ["guides", "python", "composition"],
    ),
    "docs/guides/programmatic-usage.md": (
        "Programmatic usage",
        "Drive Agent, AgentSession, TerrariumRuntime, and KohakuManager from your own Python code.",
        ["guides", "python", "embedding"],
    ),
    "docs/guides/sessions.md": (
        "Sessions and resume",
        "How .kohakutr session files work, how to resume a creature, and how to replay conversation history.",
        ["guides", "session", "persistence"],
    ),
    "docs/guides/memory.md": (
        "Memory",
        "FTS5 + vector memory over the session store, embedding provider choice, and retrieval patterns.",
        ["guides", "memory", "embedding"],
    ),
    "docs/guides/plugins.md": (
        "Plugins",
        "Prompt plugins and lifecycle plugins — what each hooks, how they compose, and when to use them.",
        ["guides", "plugin", "extending"],
    ),
    "docs/guides/custom-modules.md": (
        "Custom modules",
        "Write and register custom inputs, triggers, tools, outputs, and sub-agents against the module protocols.",
        ["guides", "extending", "module"],
    ),
    "docs/guides/mcp.md": (
        "MCP",
        "Connect Model Context Protocol servers (stdio / HTTP / SSE) and surface their tools to your creatures.",
        ["guides", "mcp", "integration"],
    ),
    "docs/guides/packages.md": (
        "Packages",
        "Installing packs via kt install, the kohaku.yaml manifest, @pkg/ references, and publishing your own pack.",
        ["guides", "package", "distribution"],
    ),
    "docs/guides/serving.md": (
        "Serving",
        "kt serve for the HTTP API + WebSocket + web dashboard, plus native desktop via kt app.",
        ["guides", "serving", "http"],
    ),
    "docs/guides/examples.md": (
        "Examples",
        "Tour of the bundled example creatures, terrariums, and code — what to read first and why.",
        ["guides", "examples"],
    ),
    "docs/guides/frontend-layout.md": (
        "Frontend layout",
        "How the Vue 3 dashboard is organised, where to extend it, and how events flow from backend to UI.",
        ["guides", "frontend", "ui"],
    ),
    # Concepts
    "docs/concepts/README.md": (
        "Concepts",
        "Mental models for creatures, terrariums, channels, triggers, plugins, and the compose algebra.",
        ["concepts", "overview"],
    ),
    "docs/concepts/boundaries.md": (
        "Boundaries",
        "The creature abstraction is a default, not a corset — where the framework bends, and when it doesn't fit at all.",
        ["concepts", "philosophy"],
    ),
    "docs/concepts/patterns.md": (
        "Patterns",
        "Recipes that fall out of combining existing modules — group chat, smart guards, adaptive watchers, output wiring.",
        ["concepts", "patterns"],
    ),
    "docs/concepts/glossary.md": (
        "Glossary",
        "Plain-English definitions for the vocabulary used across the docs.",
        ["concepts", "glossary", "reference"],
    ),
    "docs/concepts/foundations/README.md": (
        "Foundations",
        "Why the framework exists, what an agent is in its model, and how the six modules wire up at runtime.",
        ["concepts", "foundations"],
    ),
    "docs/concepts/foundations/why-kohakuterrarium.md": (
        "Why KohakuTerrarium",
        "The observation that every agent product re-implements the same substrate — and the framework-shaped response.",
        ["concepts", "foundations", "philosophy"],
    ),
    "docs/concepts/foundations/what-is-an-agent.md": (
        "What is an agent",
        "Build up a creature from a chat bot in four stages — controller, tools, triggers, sub-agents.",
        ["concepts", "foundations", "creature"],
    ),
    "docs/concepts/foundations/composing-an-agent.md": (
        "Composing an agent",
        "How the six creature modules interact at runtime through one TriggerEvent envelope.",
        ["concepts", "foundations", "runtime"],
    ),
    "docs/concepts/modules/README.md": (
        "Modules",
        "One doc per creature module — controller, input, trigger, tool, sub-agent, output, plus cross-cutters.",
        ["concepts", "modules", "overview"],
    ),
    "docs/concepts/modules/controller.md": (
        "Controller",
        "The reasoning loop that streams from the LLM, parses tool calls, and dispatches feedback.",
        ["concepts", "module", "controller"],
    ),
    "docs/concepts/modules/input.md": (
        "Input",
        "The specific-case trigger that carries user messages into the event queue.",
        ["concepts", "module", "input"],
    ),
    "docs/concepts/modules/trigger.md": (
        "Trigger",
        "Anything that wakes the controller without explicit user input — timers, idle, channels, webhooks, monitors.",
        ["concepts", "module", "trigger"],
    ),
    "docs/concepts/modules/tool.md": (
        "Tool",
        "Named capabilities the LLM can invoke — shell commands, file edits, web searches, and more.",
        ["concepts", "module", "tool"],
    ),
    "docs/concepts/modules/sub-agent.md": (
        "Sub-agent",
        "Nested creatures spawned by a parent for bounded tasks, with their own context and a subset of tools.",
        ["concepts", "module", "sub-agent"],
    ),
    "docs/concepts/modules/output.md": (
        "Output",
        "How a creature talks back — the output router that fans text, activity, and structured events to sinks.",
        ["concepts", "module", "output"],
    ),
    "docs/concepts/modules/channel.md": (
        "Channel",
        "Named message pipes — queue vs. broadcast — that underpin multi-agent and cross-module communication.",
        ["concepts", "module", "channel", "multi-agent"],
    ),
    "docs/concepts/modules/session-and-environment.md": (
        "Session & environment",
        "Per-creature private state (session) vs. terrarium-shared state (environment) and how they interact.",
        ["concepts", "module", "session", "environment"],
    ),
    "docs/concepts/modules/memory-and-compaction.md": (
        "Memory & compaction",
        "How the session store doubles as a searchable memory, and how non-blocking compaction keeps context in budget.",
        ["concepts", "memory", "compaction"],
    ),
    "docs/concepts/modules/plugin.md": (
        "Plugin",
        "Code that modifies the connections between modules without forking them — prompt plugins and lifecycle plugins.",
        ["concepts", "module", "plugin"],
    ),
    "docs/concepts/multi-agent/README.md": (
        "Multi-agent",
        "Two axes — vertical (sub-agents) and horizontal (terrarium + channels + output wiring) — and when to pick which.",
        ["concepts", "multi-agent", "overview"],
    ),
    "docs/concepts/multi-agent/terrarium.md": (
        "Terrarium",
        "The horizontal wiring layer — channels for optional traffic, output wiring for deterministic edges, hot-plug and observation on top.",
        ["concepts", "multi-agent", "terrarium"],
    ),
    "docs/concepts/multi-agent/root-agent.md": (
        "Root agent",
        "A creature outside the terrarium that represents the user — user-facing surface, management toolset, topology awareness.",
        ["concepts", "multi-agent", "root"],
    ),
    "docs/concepts/python-native/README.md": (
        "Python-native",
        "Agents as first-class async Python values, and the algebra that stitches them into pipelines.",
        ["concepts", "python", "overview"],
    ),
    "docs/concepts/python-native/agent-as-python-object.md": (
        "Agent as a Python object",
        "Why every agent is a Python object, what that unlocks, and how embedding is different from running a CLI.",
        ["concepts", "python", "embedding"],
    ),
    "docs/concepts/python-native/composition-algebra.md": (
        "Compose algebra",
        "Four operators and a set of combinators that treat agents and async callables as composable units.",
        ["concepts", "python", "composition"],
    ),
    "docs/concepts/impl-notes/README.md": (
        "Implementation notes",
        "Deep dives into how specific subsystems actually work — for contributors and curious readers.",
        ["concepts", "impl-notes", "internals"],
    ),
    "docs/concepts/impl-notes/prompt-aggregation.md": (
        "Prompt aggregation",
        "How the system prompt is assembled from personality, tool list, framework hints, and on-demand skills.",
        ["concepts", "impl-notes", "prompt"],
    ),
    "docs/concepts/impl-notes/stream-parser.md": (
        "Stream parser",
        "State-machine parsing of LLM output into text, tool calls, sub-agent dispatches, and framework commands.",
        ["concepts", "impl-notes", "parser"],
    ),
    "docs/concepts/impl-notes/non-blocking-compaction.md": (
        "Non-blocking compaction",
        "How the controller keeps running while the summariser rebuilds a compacted conversation in the background.",
        ["concepts", "impl-notes", "compaction"],
    ),
    "docs/concepts/impl-notes/session-persistence.md": (
        "Session persistence",
        "The .kohakutr file format, what's stored per creature, and how resume rebuilds conversation state.",
        ["concepts", "impl-notes", "persistence"],
    ),
    # Reference
    "docs/reference/README.md": (
        "Reference",
        "Full-surface specifications — every field, command, endpoint, hook, and Python entry point.",
        ["reference", "overview"],
    ),
    "docs/reference/cli.md": (
        "CLI",
        "Every kt subcommand — run, resume, login, install, list, info, model, embedding, search, terrarium, serve, app.",
        ["reference", "cli"],
    ),
    "docs/reference/configuration.md": (
        "Configuration",
        "Every configuration field for creatures, terrariums, LLM profiles, MCP servers, compaction, plugins, and output wiring.",
        ["reference", "config"],
    ),
    "docs/reference/builtins.md": (
        "Built-ins",
        "The bundled tools, sub-agents, triggers, inputs, and outputs — argument shapes, behaviours, and defaults.",
        ["reference", "builtins"],
    ),
    "docs/reference/python.md": (
        "Python API",
        "The kohakuterrarium package surface — Agent, AgentSession, TerrariumRuntime, compose, and testing helpers.",
        ["reference", "python", "api"],
    ),
    "docs/reference/plugin-hooks.md": (
        "Plugin hooks",
        "Every lifecycle hook plugins can register, when it fires, and what payload it receives.",
        ["reference", "plugin", "hooks"],
    ),
    "docs/reference/http.md": (
        "HTTP API",
        "The kt serve REST endpoints and WebSocket channels, with request / response shapes.",
        ["reference", "http", "api"],
    ),
    # Dev
    "docs/dev/README.md": (
        "Development",
        "Contributor-facing docs — internals, dep graph, frontend, and the test strategy.",
        ["dev", "overview"],
    ),
    "docs/dev/internals.md": (
        "Internals",
        "How the runtime fits together — event queue, controller loop, executor, subagent manager, plugin wrap.",
        ["dev", "internals"],
    ),
    "docs/dev/dependency-graph.md": (
        "Dependency graph",
        "Module import-direction invariants and the tests that enforce them.",
        ["dev", "internals", "architecture"],
    ),
    "docs/dev/frontend.md": (
        "Frontend",
        "Vue 3 dashboard layout, state stores, WebSocket plumbing, and how to contribute UI changes.",
        ["dev", "frontend"],
    ),
    "docs/dev/testing.md": (
        "Testing",
        "Test layout, the ScriptedLLM and TestAgentBuilder helpers, and how to write deterministic agent tests.",
        ["dev", "testing"],
    ),
}


def frontmatter(title: str, summary: str, tags: list[str]) -> str:
    tag_block = "\n".join(f"  - {t}" for t in tags)
    return (
        "---\n"
        f"title: {title}\n"
        f"summary: {summary}\n"
        "tags:\n"
        f"{tag_block}\n"
        "---\n\n"
    )


def main() -> None:
    added = 0
    skipped = 0
    missing = 0
    for path, (title, summary, tags) in META.items():
        if not os.path.exists(path):
            print(f"MISSING: {path}")
            missing += 1
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if content.startswith("---\n"):
            skipped += 1
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(frontmatter(title, summary, tags) + content)
        added += 1
    print(
        f"Added frontmatter to {added} files. "
        f"Skipped {skipped} (already had frontmatter). Missing {missing}."
    )


if __name__ == "__main__":
    main()
