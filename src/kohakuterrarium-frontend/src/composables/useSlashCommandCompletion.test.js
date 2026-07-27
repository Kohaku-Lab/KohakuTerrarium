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

  it("hides skills shadowed by command names or aliases", async () => {
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [{ name: "review", aliases: ["r"], description: "Review command" }],
          skills: [
            { name: "review", enabled: true },
            { name: "R", enabled: true },
            { name: "standalone", enabled: true },
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
      "command:review",
      "skill:standalone",
    ])
  })
})
