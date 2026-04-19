export default {
  docsDir: "./docs",
  projectRoot: ".",
  homePage: "README.md",
  site: {
    title: "KohakuTerrarium",
    description:
      "A universal framework for building any kind of self-driven agent system. One substrate for creatures, sub-agents, terrariums, tools, triggers, channels, plugins, memory, and I/O — so teams stop rebuilding the substrate every time they want a new agent shape.",
    editBaseUrl:
      "https://github.com/Kohaku-Lab/KohakuTerrarium/edit/main/docs/",
  },
  home: {
    kicker: "Framework for agents, not another agent",
    title: "KohakuTerrarium Docs",
    description:
      "Creatures compose horizontally into terrariums through channels and output wiring, vertically through sub-agents, and natively into Python via the compose algebra. The docs walk the concept model, the practical guides, the full configuration / API reference, and the runnable tutorials.",
    actions: [
      { text: "Getting started", to: "/docs/guides/getting-started" },
      {
        text: "GitHub",
        href: "https://github.com/Kohaku-Lab/KohakuTerrarium",
        variant: "secondary",
      },
      {
        text: "kt-biome (showcase pack)",
        href: "https://github.com/Kohaku-Lab/kt-biome",
        variant: "secondary",
      },
    ],
    cards: [
      {
        title: "What is an agent?",
        description:
          "The six-module creature derivation — controller, input, trigger, tool, sub-agent, output — built up from a chat bot in four stages.",
        to: "/docs/concepts/foundations/what-is-an-agent",
      },
      {
        title: "First creature tutorial",
        description:
          "Author a creature config, run it in CLI / TUI / web, customise the system prompt and tools.",
        to: "/docs/tutorials/first-creature",
      },
      {
        title: "First terrarium tutorial",
        description:
          "Wire two creatures through channels and output_wiring, add a root to get a single conversational surface.",
        to: "/docs/tutorials/first-terrarium",
      },
      {
        title: "Terrariums guide",
        description:
          "Channels vs. output wiring, root agents, hot-plug, observation — the practical how-to for horizontal multi-agent.",
        to: "/docs/guides/terrariums",
      },
      {
        title: "Configuration reference",
        description:
          "Every field for creatures, terrariums, LLM profiles, MCP servers, compaction, plugins, and output wiring.",
        to: "/docs/reference/configuration",
      },
      {
        title: "ROADMAP",
        description:
          "What shipped in 1.0.x and what we're still exploring for terrariums, UI, memory, and integrations.",
        href: "https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/ROADMAP.md",
      },
    ],
  },
  markdown: {
    stripTitleHeading: true,
  },
  sidebar: [
    {
      text: "Overview",
      items: ["README.md"],
    },
    {
      text: "Tutorials",
      items: [
        "tutorials/README.md",
        "tutorials/first-creature.md",
        "tutorials/first-custom-tool.md",
        "tutorials/first-plugin.md",
        "tutorials/first-terrarium.md",
        "tutorials/first-python-embedding.md",
      ],
    },
    {
      text: "Guides",
      items: [
        "guides/README.md",
        "guides/getting-started.md",
        "guides/configuration.md",
        "guides/creatures.md",
        "guides/terrariums.md",
        "guides/composition.md",
        "guides/programmatic-usage.md",
        "guides/sessions.md",
        "guides/memory.md",
        "guides/plugins.md",
        "guides/custom-modules.md",
        "guides/mcp.md",
        "guides/packages.md",
        "guides/serving.md",
        "guides/examples.md",
        "guides/frontend-layout.md",
      ],
    },
    {
      text: "Concepts",
      items: [
        "concepts/README.md",
        {
          text: "Foundations",
          items: [
            "concepts/foundations/README.md",
            "concepts/foundations/why-kohakuterrarium.md",
            "concepts/foundations/what-is-an-agent.md",
            "concepts/foundations/composing-an-agent.md",
          ],
        },
        {
          text: "Modules",
          items: [
            "concepts/modules/README.md",
            "concepts/modules/controller.md",
            "concepts/modules/input.md",
            "concepts/modules/trigger.md",
            "concepts/modules/tool.md",
            "concepts/modules/sub-agent.md",
            "concepts/modules/output.md",
            "concepts/modules/channel.md",
            "concepts/modules/session-and-environment.md",
            "concepts/modules/memory-and-compaction.md",
            "concepts/modules/plugin.md",
          ],
        },
        {
          text: "Multi-agent",
          items: [
            "concepts/multi-agent/README.md",
            "concepts/multi-agent/terrarium.md",
            "concepts/multi-agent/root-agent.md",
          ],
        },
        {
          text: "Python-native",
          items: [
            "concepts/python-native/README.md",
            "concepts/python-native/agent-as-python-object.md",
            "concepts/python-native/composition-algebra.md",
          ],
        },
        "concepts/patterns.md",
        "concepts/boundaries.md",
        "concepts/glossary.md",
        {
          text: "Implementation notes",
          items: [
            "concepts/impl-notes/README.md",
            "concepts/impl-notes/prompt-aggregation.md",
            "concepts/impl-notes/stream-parser.md",
            "concepts/impl-notes/non-blocking-compaction.md",
            "concepts/impl-notes/session-persistence.md",
          ],
        },
      ],
    },
    {
      text: "Reference",
      items: [
        "reference/README.md",
        "reference/cli.md",
        "reference/configuration.md",
        "reference/builtins.md",
        "reference/python.md",
        "reference/plugin-hooks.md",
        "reference/http.md",
      ],
    },
    {
      text: "Development",
      items: [
        "dev/README.md",
        "dev/internals.md",
        "dev/dependency-graph.md",
        "dev/frontend.md",
        "dev/testing.md",
      ],
    },
  ],
}
