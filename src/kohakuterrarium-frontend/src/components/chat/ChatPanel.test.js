import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { ElMessageBox } from "element-plus"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  setActivePinia(createPinia())
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
    const wrapper = mount(ChatPanel, {
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
    const wrapper = mount(ChatPanel, {
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
      const wrapper = mount(ChatPanel, {
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
      const wrapper = mount(ChatPanel, {
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
    const wrapper = mount(ChatPanel, {
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
    const wrapper = mount(ChatPanel, {
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
      const wrapper = mount(ChatPanel, {
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
    const wrapper = mount(ChatPanel, {
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
    const wrapper = mount(ChatPanel, {
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
