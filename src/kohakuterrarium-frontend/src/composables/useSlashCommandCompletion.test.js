import { flushPromises } from "@vue/test-utils"
import { nextTick, reactive, ref } from "vue"
import { describe, expect, it, vi } from "vitest"

import { useSlashCommandCompletion } from "./useSlashCommandCompletion"

describe("useSlashCommandCompletion", () => {
  it("keeps the menu open for a loaded slash query with no matches", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [{ name: "help", aliases: [], description: "Show help" }],
          skills: [],
        },
      },
      loadCommandInventory: vi.fn().mockResolvedValue(undefined),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    inputText.value = "/missing"
    await nextTick()
    await flushPromises()

    expect(completion.loading.value).toBe(false)
    expect(completion.entries.value).toEqual([])
    expect(completion.open.value).toBe(true)
  })

  it("shows only inventory /goal and enabled skills outside the full command namespace", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [
            { name: "model", aliases: ["llm"], description: "Switch model" },
            { name: "goal", aliases: [], description: "Manage goals" },
            { name: "status", aliases: ["info"], description: "Show status" },
            { name: "compact", aliases: [], description: "Compact context" },
          ],
          skills: [
            { name: "MODEL", enabled: true },
            { name: "Info", enabled: true },
            { name: "disabled-review", enabled: false },
            { name: "manual-only", enabled: true, invocation_blocked: true },
            { name: "research", enabled: true },
          ],
        },
      },
      loadCommandInventory: vi.fn().mockResolvedValue(undefined),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    await nextTick()

    expect(completion.entries.value.map((entry) => `${entry.type}:${entry.name}`)).toEqual([
      "command:goal",
      "skill:research",
    ])
  })

  it("does not synthesize /goal when the live inventory does not contain it", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [{ name: "status", aliases: ["info"], description: "Show status" }],
          skills: [{ name: "research", enabled: true }],
        },
      },
      loadCommandInventory: vi.fn().mockResolvedValue(undefined),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    await nextTick()

    expect(completion.entries.value.map((entry) => `${entry.type}:${entry.name}`)).toEqual([
      "skill:research",
    ])
  })

  it("filters the visible /goal and skills without revealing hidden commands", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [
            { name: "goal", aliases: [], description: "Manage goals" },
            { name: "status", aliases: ["info"], description: "Show status" },
          ],
          skills: [{ name: "research", enabled: true }],
        },
      },
      loadCommandInventory: vi.fn().mockResolvedValue(undefined),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/go")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    await nextTick()
    expect(completion.entries.value.map((entry) => entry.name)).toEqual(["goal"])

    inputText.value = "/stat"
    await nextTick()
    expect(completion.entries.value).toEqual([])

    inputText.value = "/sea"
    await nextTick()
    expect(completion.entries.value.map((entry) => entry.name)).toEqual(["research"])
  })

  it("dismisses the current query until the input changes or the menu is reopened", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [{ name: "goal", aliases: [], description: "Manage goals" }],
          skills: [],
        },
      },
      loadCommandInventory: vi.fn().mockResolvedValue(undefined),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    await nextTick()
    expect(completion.open.value).toBe(true)

    completion.dismiss()
    await nextTick()
    expect(completion.open.value).toBe(false)
    expect(chat.markSlashTarget).toHaveBeenLastCalledWith(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      null,
    )

    inputText.value = "/go"
    await nextTick()
    expect(completion.open.value).toBe(true)

    completion.dismiss()
    expect(completion.open.value).toBe(false)
    completion.reopen()
    expect(completion.open.value).toBe(true)
  })
})
