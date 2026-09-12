import { flushPromises } from "@vue/test-utils"
import { effectScope, nextTick, reactive, ref } from "vue"
import { describe, expect, it, vi } from "vitest"

import { useSlashCommandCompletion } from "./useSlashCommandCompletion"

// A reactive chat double whose markSlashTarget mirrors the real store: it stores a
// fresh object and clears on null, so marker-object ownership is meaningfully testable.
function ownershipChat() {
  return reactive({
    commandInventoryByTab: {
      kohaku: {
        commands: [{ name: "goal", aliases: [], description: "Manage goals" }],
        skills: [{ name: "research", enabled: true }],
      },
    },
    _instanceGeneration: 1,
    _slashTargetByTab: {},
    loadCommandInventory: vi.fn().mockResolvedValue(undefined),
    markSlashTarget(tab, entry) {
      const key = typeof tab === "string" ? tab : tab?.key
      if (entry) this._slashTargetByTab[key] = { type: entry.type || entry.kind, name: entry.name }
      else delete this._slashTargetByTab[key]
    },
  })
}

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

  it("keeps the pending load's state when a superseded load fails", async () => {
    let rejectOld
    let rejectCurrent
    const loadCommandInventory = vi
      .fn()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => (rejectOld = reject)))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => (rejectCurrent = reject)))
    const chat = reactive({
      commandInventoryByTab: {
        kohaku: {
          commands: [{ name: "goal", aliases: [], description: "Manage goals" }],
          skills: [],
        },
        reviewer: { commands: [], skills: [] },
      },
      _instanceGeneration: 1,
      loadCommandInventory,
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    inputText.value = "/g"
    await nextTick()
    expect(loadCommandInventory).toHaveBeenCalledTimes(1)

    activeTabKey.value = "reviewer"
    await nextTick()
    expect(loadCommandInventory).toHaveBeenCalledTimes(2)
    expect(completion.loading.value).toBe(true)

    rejectOld(new Error("old tab failed"))
    await flushPromises()
    expect(completion.loading.value).toBe(true)
    expect(completion.error.value).toBe(null)

    rejectCurrent(new Error("current tab failed"))
    await flushPromises()
    expect(completion.loading.value).toBe(false)
    expect(completion.error.value).toBe("current tab failed")
  })

  it("does not repaint loading or error after the scope is disposed", async () => {
    let rejectLoad
    const chat = reactive({
      commandInventoryByTab: { kohaku: { commands: [], skills: [] } },
      loadCommandInventory: vi.fn(() => new Promise((_resolve, reject) => (rejectLoad = reject))),
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("")
    const activeTabKey = ref("kohaku")
    const scope = effectScope()
    const completion = scope.run(() => useSlashCommandCompletion({ chat, inputText, activeTabKey }))

    inputText.value = "/g"
    await nextTick()
    expect(completion.loading.value).toBe(true)

    scope.stop()
    rejectLoad(new Error("late failure"))
    await flushPromises()

    expect(completion.loading.value).toBe(true)
    expect(completion.error.value).toBe(null)
  })

  it("reloads the inventory through the store when the instance generation changes", async () => {
    const loadCommandInventory = vi.fn().mockResolvedValue(undefined)
    const chat = reactive({
      commandInventoryByTab: { kohaku: { commands: [], skills: [] } },
      _instanceGeneration: 1,
      loadCommandInventory,
      markSlashTarget: vi.fn(),
    })
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    useSlashCommandCompletion({ chat, inputText, activeTabKey })

    inputText.value = "/g"
    await nextTick()
    expect(loadCommandInventory).toHaveBeenCalledTimes(1)

    chat._instanceGeneration = 2
    await nextTick()
    expect(loadCommandInventory).toHaveBeenCalledTimes(2)
    expect(loadCommandInventory).toHaveBeenLastCalledWith(
      { key: "kohaku", creature: "kohaku", type: "creature" },
      { force: false },
    )
  })

  it("releases only the exact marker object it owns, not a newer same-named marker", () => {
    const chat = ownershipChat()
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    completion.choose({ type: "skill", name: "research" })
    expect(chat._slashTargetByTab.kohaku).toMatchObject({ type: "skill", name: "research" })

    completion.releaseOwnedTarget()
    expect(chat._slashTargetByTab.kohaku).toBeUndefined()

    // A newer interaction marks the same kind/name with a fresh object.
    completion.choose({ type: "skill", name: "research" })
    const owned = chat._slashTargetByTab.kohaku
    chat.markSlashTarget("kohaku", { type: "skill", name: "research" })
    const replacement = chat._slashTargetByTab.kohaku
    expect(replacement).not.toBe(owned)

    completion.releaseOwnedTarget()
    expect(chat._slashTargetByTab.kohaku).toBe(replacement)
  })

  it("does not release its marker after the instance generation changes", () => {
    const chat = ownershipChat()
    const inputText = ref("/")
    const activeTabKey = ref("kohaku")
    const completion = useSlashCommandCompletion({ chat, inputText, activeTabKey })

    completion.choose({ type: "skill", name: "research" })
    chat._instanceGeneration = 2
    completion.releaseOwnedTarget()

    expect(chat._slashTargetByTab.kohaku).toBeDefined()
  })
})
