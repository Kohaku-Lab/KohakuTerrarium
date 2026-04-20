/**
 * KohakuTerrarium docs site configuration.
 *
 * Multi-locale: each locale has its own sidebar and landing-page copy,
 * but the physical Markdown files live under `docs/<locale>/` and can
 * be a subset — pages missing in a non-default locale fall back to
 * the default locale (English) per the `missingTranslation: "fallback"`
 * policy. The sidebar still lists every page so navigation stays
 * symmetric across locales.
 */

// ---------------------------------------------------------------------------
// Shared sidebar shape — the same ordered page list for every locale.
// Per-locale sidebar objects only differ in the `text` labels.
// ---------------------------------------------------------------------------

const sidebarStructure = {
  overview: ["README.md"],
  tutorials: [
    "tutorials/README.md",
    "tutorials/first-creature.md",
    "tutorials/first-custom-tool.md",
    "tutorials/first-plugin.md",
    "tutorials/first-terrarium.md",
    "tutorials/first-python-embedding.md",
  ],
  guides: [
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
  conceptsRoot: ["concepts/README.md"],
  conceptsFoundations: [
    "concepts/foundations/README.md",
    "concepts/foundations/why-kohakuterrarium.md",
    "concepts/foundations/what-is-an-agent.md",
    "concepts/foundations/composing-an-agent.md",
  ],
  conceptsModules: [
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
  conceptsMultiAgent: [
    "concepts/multi-agent/README.md",
    "concepts/multi-agent/terrarium.md",
    "concepts/multi-agent/root-agent.md",
  ],
  conceptsPythonNative: [
    "concepts/python-native/README.md",
    "concepts/python-native/agent-as-python-object.md",
    "concepts/python-native/composition-algebra.md",
  ],
  conceptsTail: [
    "concepts/patterns.md",
    "concepts/boundaries.md",
    "concepts/glossary.md",
  ],
  conceptsImplNotes: [
    "concepts/impl-notes/README.md",
    "concepts/impl-notes/prompt-aggregation.md",
    "concepts/impl-notes/stream-parser.md",
    "concepts/impl-notes/non-blocking-compaction.md",
    "concepts/impl-notes/session-persistence.md",
  ],
  reference: [
    "reference/README.md",
    "reference/cli.md",
    "reference/configuration.md",
    "reference/builtins.md",
    "reference/python.md",
    "reference/plugin-hooks.md",
    "reference/http.md",
  ],
  dev: [
    "dev/README.md",
    "dev/internals.md",
    "dev/dependency-graph.md",
    "dev/frontend.md",
    "dev/testing.md",
  ],
}

// ---------------------------------------------------------------------------
// English sidebar
// ---------------------------------------------------------------------------

const enSidebar = [
  { text: "Overview", items: sidebarStructure.overview },
  { text: "Tutorials", items: sidebarStructure.tutorials },
  { text: "Guides", items: sidebarStructure.guides },
  {
    text: "Concepts",
    items: [
      ...sidebarStructure.conceptsRoot,
      { text: "Foundations", items: sidebarStructure.conceptsFoundations },
      { text: "Modules", items: sidebarStructure.conceptsModules },
      { text: "Multi-agent", items: sidebarStructure.conceptsMultiAgent },
      { text: "Python-native", items: sidebarStructure.conceptsPythonNative },
      ...sidebarStructure.conceptsTail,
      { text: "Implementation notes", items: sidebarStructure.conceptsImplNotes },
    ],
  },
  { text: "Reference", items: sidebarStructure.reference },
  { text: "Development", items: sidebarStructure.dev },
]

// ---------------------------------------------------------------------------
// zh-TW sidebar — same file paths, translated labels
// ---------------------------------------------------------------------------

const zhTWSidebar = [
  { text: "總覽", items: sidebarStructure.overview },
  { text: "教學", items: sidebarStructure.tutorials },
  { text: "使用指南", items: sidebarStructure.guides },
  {
    text: "核心概念",
    items: [
      ...sidebarStructure.conceptsRoot,
      { text: "基礎", items: sidebarStructure.conceptsFoundations },
      { text: "模組", items: sidebarStructure.conceptsModules },
      { text: "多代理系統", items: sidebarStructure.conceptsMultiAgent },
      { text: "Python 原生整合", items: sidebarStructure.conceptsPythonNative },
      ...sidebarStructure.conceptsTail,
      { text: "實作筆記", items: sidebarStructure.conceptsImplNotes },
    ],
  },
  { text: "參考", items: sidebarStructure.reference },
  { text: "開發", items: sidebarStructure.dev },
]

const zhCNSidebar = [
  { text: "总览", items: sidebarStructure.overview },
  { text: "教程", items: sidebarStructure.tutorials },
  { text: "使用指南", items: sidebarStructure.guides },
  {
    text: "核心概念",
    items: [
      ...sidebarStructure.conceptsRoot,
      { text: "基础", items: sidebarStructure.conceptsFoundations },
      { text: "模块", items: sidebarStructure.conceptsModules },
      { text: "多智能体系统", items: sidebarStructure.conceptsMultiAgent },
      { text: "Python 原生整合", items: sidebarStructure.conceptsPythonNative },
      ...sidebarStructure.conceptsTail,
      { text: "实现笔记", items: sidebarStructure.conceptsImplNotes },
    ],
  },
  { text: "参考", items: sidebarStructure.reference },
  { text: "开发", items: sidebarStructure.dev },
]

// ---------------------------------------------------------------------------
// Home-page cards (shared layout, per-locale strings)
// ---------------------------------------------------------------------------

const enHomeCards = [
  {
    title: "What is an agent?",
    description:
      "The six-module creature derivation — controller, input, trigger, tool, sub-agent, output — built up from a chat bot in four stages.",
    to: "/docs/concepts/foundations/what-is-an-agent",
  },
  {
    title: "First creature tutorial",
    description:
      "Author a creature config, run it in CLI / TUI / web modes, customise the prompt and tools.",
    to: "/docs/tutorials/first-creature",
  },
  {
    title: "First terrarium tutorial",
    description:
      "Wire two creatures through channels and output_wiring, then add a root to get a single conversational surface.",
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
]

const zhTWHomeCards = [
  {
    title: "什麼是 agent？",
    description:
      "從聊天機器人出發，分四個階段建立生物的六模組結構：控制器、輸入、觸發器、工具、子代理、輸出。",
    to: "/docs/concepts/foundations/what-is-an-agent",
  },
  {
    title: "第一隻生物",
    description:
      "撰寫生物設定，在 CLI / TUI / 網頁模式中執行，調整提示詞與工具。",
    to: "/docs/tutorials/first-creature",
  },
  {
    title: "第一個生態瓶",
    description:
      "用頻道與 output_wiring 把兩隻生物串起來，再加一個 root 提供單一對話介面。",
    to: "/docs/tutorials/first-terrarium",
  },
  {
    title: "生態瓶使用指南",
    description:
      "頻道 vs. 輸出接線、root 代理、熱插拔、觀察機制 — 橫向多代理的實務 how-to。",
    to: "/docs/guides/terrariums",
  },
  {
    title: "設定參考",
    description:
      "生物、生態瓶、LLM 設定檔、MCP 伺服器、上下文壓縮、外掛、輸出接線的所有欄位。",
    to: "/docs/reference/configuration",
  },
  {
    title: "ROADMAP",
    description:
      "1.0.x 已釋出的項目，以及生態瓶、UI、記憶、整合方面仍在探索的方向。",
    href: "https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/ROADMAP.md",
  },
]

const zhCNHomeCards = [
  {
    title: "什么是 agent？",
    description:
      "从聊天机器人出发，分四个阶段建立生物的六模块结构：控制器、输入、触发器、工具、子代理、输出。",
    to: "/docs/concepts/foundations/what-is-an-agent",
  },
  {
    title: "第一只生物",
    description:
      "编写生物配置，在 CLI / TUI / 网页模式中运行，调整提示词与工具。",
    to: "/docs/tutorials/first-creature",
  },
  {
    title: "第一个生态瓶",
    description:
      "用频道与 output_wiring 把两只生物串起来，再加一个 root 提供单一对话界面。",
    to: "/docs/tutorials/first-terrarium",
  },
  {
    title: "生态瓶使用指南",
    description:
      "频道 vs. 输出接线、root 代理、热插拔、观察机制 — 横向多智能体的实用 how-to。",
    to: "/docs/guides/terrariums",
  },
  {
    title: "配置参考",
    description:
      "生物、生态瓶、LLM 配置文件、MCP 服务器、上下文压缩、插件、输出接线的所有字段。",
    to: "/docs/reference/configuration",
  },
  {
    title: "ROADMAP",
    description:
      "1.0.x 已发布的项目，以及生态瓶、UI、记忆、集成方面仍在探索的方向。",
    href: "https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/ROADMAP.md",
  },
]

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export default {
  docsDir: "./docs",
  projectRoot: ".",
  defaultLocale: "en",
  // Pages missing in a non-default locale fall back to English.
  // Every locale's sidebar can list the full set of pages — missing
  // files silently render the English content under the localised URL.
  missingTranslation: "fallback",
  markdown: {
    stripTitleHeading: true,
  },
  locales: {
    en: {
      label: "English",
      docsSubdir: "en",
      homePage: "README.md",
      ui: "en",
      site: {
        title: "KohakuTerrarium",
        description:
          "A universal framework for building self-driven agent systems. One substrate for creatures, sub-agents, terrariums, tools, triggers, channels, plugins, memory, and I/O.",
        editBaseUrl:
          "https://github.com/Kohaku-Lab/KohakuTerrarium/edit/main/docs/en/",
        favicon: "./docs-assets/favicon.png",
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
        cards: enHomeCards,
      },
      sidebar: enSidebar,
    },
    "zh-TW": {
      label: "繁體中文",
      docsSubdir: "zh-TW",
      homePage: "README.md",
      ui: "zh-TW",
      site: {
        title: "KohakuTerrarium",
        description:
          "一個通用的代理系統框架。生物、子代理、生態瓶、工具、觸發器、頻道、外掛、記憶、I/O 共用同一套底層機制。",
        editBaseUrl:
          "https://github.com/Kohaku-Lab/KohakuTerrarium/edit/main/docs/zh-TW/",
        favicon: "./docs-assets/favicon.png",
      },
      home: {
        kicker: "框架是用來建 agent 的,而不是又一個 agent",
        title: "KohakuTerrarium 文件",
        description:
          "生物透過頻道與輸出接線橫向組合為生態瓶,透過子代理進行縱向分解,並可藉由 compose 代數原生嵌入 Python。文件涵蓋概念模型、實務指南、完整設定與 API 參考,以及可實際操作的教學。",
        actions: [
          { text: "快速開始", to: "/docs/guides/getting-started" },
          {
            text: "GitHub",
            href: "https://github.com/Kohaku-Lab/KohakuTerrarium",
            variant: "secondary",
          },
          {
            text: "kt-biome (官方套件)",
            href: "https://github.com/Kohaku-Lab/kt-biome",
            variant: "secondary",
          },
        ],
        cards: zhTWHomeCards,
      },
      sidebar: zhTWSidebar,
    },
    "zh-CN": {
      label: "简体中文",
      docsSubdir: "zh-CN",
      homePage: "README.md",
      ui: "zh-CN",
      site: {
        title: "KohakuTerrarium",
        description:
          "一个通用的代理系统框架。生物、子代理、生态瓶、工具、触发器、频道、插件、记忆、I/O 共用同一套底层机制。",
        editBaseUrl:
          "https://github.com/Kohaku-Lab/KohakuTerrarium/edit/main/docs/zh-CN/",
        favicon: "./docs-assets/favicon.png",
      },
      home: {
        kicker: "框架是用来建 agent 的，不是又一个 agent",
        title: "KohakuTerrarium 文档",
        description:
          "生物通过频道与输出接线横向组成生态瓶，通过子代理进行纵向分解，并可借由 compose 代数原生嵌入 Python。文档涵盖概念模型、实用指南、完整配置与 API 参考，以及可实际操作的教程。",
        actions: [
          { text: "快速开始", to: "/docs/guides/getting-started" },
          {
            text: "GitHub",
            href: "https://github.com/Kohaku-Lab/KohakuTerrarium",
            variant: "secondary",
          },
          {
            text: "kt-biome (官方套件)",
            href: "https://github.com/Kohaku-Lab/kt-biome",
            variant: "secondary",
          },
        ],
        cards: zhCNHomeCards,
      },
      sidebar: zhCNSidebar,
    },
  },
}
