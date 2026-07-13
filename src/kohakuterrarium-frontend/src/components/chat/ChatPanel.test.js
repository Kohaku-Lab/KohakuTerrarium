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
  it("confirms typed /clear before executing its follow-up command", async () => {
    const command = vi
      .spyOn(terrariumAPI, "executeCreatureCommand")
      .mockResolvedValueOnce({
        output: "Clear 3 messages?",
        data: {
          type: "confirm",
          message: "Clear 3 messages from conversation context?",
          action: "clear",
          action_args: "--force",
        },
      })
      .mockResolvedValueOnce({
        output: "Conversation cleared",
        data: { type: "notify", message: "Context cleared", level: "success" },
      })
    let acceptConfirm
    const confirm = vi
      .spyOn(ElMessageBox, "confirm")
      .mockImplementation(() => new Promise((resolve) => (acceptConfirm = resolve)))
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
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
    await wrapper.find("textarea").setValue("/clear")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(command).toHaveBeenNthCalledWith(1, "graph_1", "kohaku", "clear", "")
    expect(confirm).toHaveBeenCalledOnce()
    chat.activeTab = "other"
    acceptConfirm("confirm")
    await flushPromises()
    expect(command).toHaveBeenNthCalledWith(2, "graph_1", "kohaku", "clear", "--force")
    command.mockRestore()
    confirm.mockRestore()
  })
})
