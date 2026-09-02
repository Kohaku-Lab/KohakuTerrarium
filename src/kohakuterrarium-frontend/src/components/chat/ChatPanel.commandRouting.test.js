import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const mountedWrappers = new Set()

function mountChatPanel(options) {
  const wrapper = mount(ChatPanel, options)
  mountedWrappers.add(wrapper)
  return wrapper
}

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  setActivePinia(createPinia())
  vi.spyOn(terrariumAPI, "getCreatureCommandInventory").mockResolvedValue({
    commands: [],
    skills: [],
  })
})

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("ChatPanel command routing", () => {
  it("does not send a slash target to a tab selected during inventory lookup", async () => {
    let resolveTarget
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    localStorage.setItem("kt.chat.draft.graph_1.reviewer", "/review")
    vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
      new Promise((resolve) => {
        resolveTarget = resolve
      }),
    )
    const execute = vi.spyOn(terrariumAPI, "executeCreatureCommand")
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [
            { name: "kohaku", status: "idle" },
            { name: "reviewer", status: "idle" },
          ],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    await wrapper.find("textarea").setValue("/review")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    chat.activeTab = "reviewer"
    await flushPromises()

    resolveTarget({ type: "skill", name: "review" })
    await flushPromises()

    expect(execute).not.toHaveBeenCalled()
    execute.mockRestore()
  })

  it.each([
    [
      "instance generation",
      (chat) => {
        chat._instanceGeneration += 1
      },
    ],
    [
      "session id",
      (chat) => {
        chat._instanceId = "session_2"
      },
    ],
    [
      "graph id",
      (chat) => {
        chat._instanceGraphId = "graph_2"
      },
    ],
  ])(
    "does not dispatch to a same-named tab when the %s changes during slash lookup",
    async (_field, changeContext) => {
      let resolveTarget
      const chat = useChatStore("session_1")
      chat._instanceGeneration = 4
      chat._instanceId = "session_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku"]
      chat.messagesByTab = { kohaku: [] }
      vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
        new Promise((resolve) => {
          resolveTarget = resolve
        }),
      )
      const execute = vi
        .spyOn(terrariumAPI, "executeCreatureCommand")
        .mockResolvedValue({ output: "unexpected" })
      const wrapper = mountChatPanel({
        props: {
          instance: {
            id: "session_1",
            graph_id: "graph_1",
            creatures: [{ name: "kohaku", status: "idle" }],
          },
        },
        global: {
          provide: { chatStore: chat },
          stubs: {
            ChatMessage: true,
            ModelSwitcher: true,
            SiteChip: true,
            StatusDot: true,
          },
        },
      })
      const textarea = wrapper.find("textarea")
      await textarea.setValue("/review focus")
      chat.markSlashTarget("kohaku", { type: "skill", name: "old-review" })
      const staleTarget = chat._slashTargetByTab.kohaku
      await wrapper.find('button[aria-label="Send message"]').trigger("click")

      changeContext(chat)
      chat.activeTab = "kohaku"
      resolveTarget({ type: "skill", name: "review" })
      await flushPromises()

      expect(execute).not.toHaveBeenCalled()
      expect(chat._slashTargetByTab.kohaku).toBeUndefined()
      expect(staleTarget).toMatchObject({ type: "skill", name: "old-review" })
      expect(textarea.element.value).toBe("/review focus")
      execute.mockRestore()
      wrapper.unmount()
    },
  )

  it("does not dispatch when another chat group takes focus during slash lookup", async () => {
    let resolveTarget
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    const sourceGroup = chat.enableGroups()
    const otherGroup = chat.splitGroup(sourceGroup, "horizontal", "after", "reviewer")
    chat.setFocusedGroup(sourceGroup)
    vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
      new Promise((resolve) => {
        resolveTarget = resolve
      }),
    )
    const execute = vi
      .spyOn(terrariumAPI, "executeCreatureCommand")
      .mockResolvedValue({ output: "unexpected" })
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [
            { name: "kohaku", status: "idle" },
            { name: "reviewer", status: "idle" },
          ],
        },
        groupId: sourceGroup,
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    const textarea = wrapper.find("textarea")
    await textarea.setValue("/review focus")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")

    chat.setFocusedGroup(otherGroup)
    expect(chat.activeTab).toBe("reviewer")
    expect(chat.groups[sourceGroup].activeTab).toBe("kohaku")
    resolveTarget({ type: "skill", name: "review" })
    await flushPromises()

    expect(execute).not.toHaveBeenCalled()
    expect(textarea.element.value).toBe("/review focus")
    execute.mockRestore()
    wrapper.unmount()
  })

  it("dismisses the slash menu without interrupting an active turn", async () => {
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.processingByTab = { kohaku: true }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "help", aliases: [], description: "Show help" }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const interrupt = vi.spyOn(chat, "interrupt").mockResolvedValue(undefined)
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [{ name: "kohaku", status: "running" }],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    const textarea = wrapper.find("textarea")
    await textarea.setValue("/")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)

    await textarea.trigger("keydown", { key: "Escape" })
    await flushPromises()

    expect(wrapper.find("#slash-command-menu").exists()).toBe(false)
    expect(textarea.attributes("aria-expanded")).toBe("false")
    expect(interrupt).not.toHaveBeenCalled()

    await textarea.trigger("blur")
    await textarea.trigger("focus")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)

    await textarea.trigger("keydown", { key: "Escape" })
    await textarea.setValue("/h")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)
    expect(interrupt).not.toHaveBeenCalled()
    interrupt.mockRestore()
    wrapper.unmount()
  })
})
