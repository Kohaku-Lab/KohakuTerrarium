import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { ElMessageBox } from "element-plus"
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
})

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.clear()
  vi.unstubAllGlobals()
})

describe("ChatPanel command results", () => {
  it("keeps clear behind the existing composer button", async () => {
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockResolvedValue({
      output: "Conversation cleared",
      data: { type: "notify", message: "Context cleared", level: "success" },
    })
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = { kohaku: { commands: [], skills: [] } }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
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
    await wrapper.find('button[aria-label="Clear context"]').trigger("click")
    await flushPromises()

    expect(command).toHaveBeenCalledWith("graph_1", "kohaku", "clear", "--force")
    expect(confirm).toHaveBeenCalledOnce()
    command.mockRestore()
    confirm.mockRestore()
  })

  it("renders /goal structured results inside the chat", async () => {
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockResolvedValue({
      output: "Goals: drive_1",
      data: {
        type: "list",
        title: "Goals",
        items: [{ label: "Ship release", description: "id=drive_1" }],
      },
    })
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "goal", aliases: [] }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
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

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(command).toHaveBeenCalledWith("graph_1", "kohaku", "goal", "list")
    expect(chat.messagesByTab.kohaku).toHaveLength(1)
    expect(chat.messagesByTab.kohaku[0]).toMatchObject({
      role: "command_result",
      command: "/goal list",
      content: "Goals: drive_1",
      data: { type: "list", title: "Goals" },
    })
    command.mockRestore()
  })

  it.each([
    ["successful", false],
    ["failed", true],
  ])(
    "anchors a %s delayed /goal result to the branch visible at dispatch",
    async (_case, rejects) => {
      let settleCommand
      const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
        new Promise((resolve, reject) => {
          settleCommand = rejects ? reject : resolve
        }),
      )
      const chat = useChatStore("graph_1")
      chat._instanceId = "graph_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku"]
      chat.eventsByTab = {
        kohaku: [
          {
            type: "user_input",
            event_id: 1,
            turn_index: 1,
            branch_id: 1,
            content: "branch one",
          },
          {
            type: "processing_start",
            event_id: 2,
            turn_index: 1,
            branch_id: 1,
          },
          {
            type: "text_chunk",
            event_id: 3,
            turn_index: 1,
            branch_id: 1,
            content: "reply",
          },
          {
            type: "processing_end",
            event_id: 4,
            turn_index: 1,
            branch_id: 1,
          },
          {
            type: "user_input",
            event_id: 5,
            turn_index: 1,
            branch_id: 2,
            content: "branch two",
          },
        ],
      }
      chat.branchViewByTab = { kohaku: { 1: 1 } }
      chat._rebuildMessages("kohaku")
      chat.commandInventoryByTab = {
        kohaku: {
          commands: [{ name: "goal", aliases: [] }],
          skills: [],
        },
      }
      chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
      const addResult = vi.spyOn(chat, "addCommandResult")
      const wrapper = mountChatPanel({
        props: {
          instance: {
            id: "graph_1",
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

      await wrapper.find("textarea").setValue("/goal list")
      await wrapper.find('button[aria-label="Send message"]').trigger("click")
      await flushPromises()
      expect(command).toHaveBeenCalledOnce()

      chat.branchViewByTab.kohaku = { 1: 2 }
      chat._rebuildMessages("kohaku")
      settleCommand(
        rejects
          ? new Error("goal failed")
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
      )
      await flushPromises()

      expect(addResult).toHaveBeenCalledWith(
        "kohaku",
        "/goal list",
        rejects
          ? { error: "goal failed" }
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
        expect.objectContaining({
          branchSelection: [[1, 1]],
          anchorIndex: 2,
        }),
      )

      command.mockRestore()
      wrapper.unmount()
    },
  )

  it.each([
    ["successful", false],
    ["failed", true],
  ])(
    "does not scroll a newly selected tab for a %s result from another tab",
    async (_case, rejects) => {
      let settleCommand
      const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
        new Promise((resolve, reject) => {
          settleCommand = rejects ? reject : resolve
        }),
      )
      const chat = useChatStore("graph_1")
      chat._instanceId = "graph_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku", "reviewer"]
      chat.messagesByTab = { kohaku: [], reviewer: [] }
      chat.eventsByTab = { kohaku: [], reviewer: [] }
      chat.commandInventoryByTab = {
        kohaku: {
          commands: [{ name: "goal", aliases: [] }],
          skills: [],
        },
      }
      chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
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

      await wrapper.find("textarea").setValue("/goal list")
      await wrapper.find('button[aria-label="Send message"]').trigger("click")
      await flushPromises()
      expect(command).toHaveBeenCalledOnce()

      chat.activeTab = "reviewer"
      await flushPromises()
      const viewport = wrapper.find(".chat-messages-viewport").element
      Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 420 })
      viewport.scrollTop = 73

      settleCommand(
        rejects
          ? new Error("goal failed")
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
      )
      await flushPromises()

      expect(chat.messagesByTab.kohaku.at(-1)).toMatchObject({
        role: "command_result",
        ...(rejects ? { error: "goal failed" } : { content: "Goals" }),
      })
      expect(viewport.scrollTop).toBe(73)
      command.mockRestore()
      wrapper.unmount()
    },
  )

  it("drops a delayed /goal result after the chat store switches sessions", async () => {
    let resolveCommand
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
      new Promise((resolve) => {
        resolveCommand = resolve
      }),
    )
    const chat = useChatStore("graph_1")
    chat._instanceGeneration = 3
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "goal", aliases: [] }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
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

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()
    expect(command).toHaveBeenCalledOnce()

    chat._instanceGeneration += 1
    chat._instanceId = "graph_2"
    chat._instanceGraphId = "graph_2"
    resolveCommand({
      output: "wrong session",
      data: { type: "list", title: "Goals", items: [] },
    })
    await flushPromises()

    expect(chat.messagesByTab.kohaku).toEqual([])
    command.mockRestore()
    wrapper.unmount()
  })
})
