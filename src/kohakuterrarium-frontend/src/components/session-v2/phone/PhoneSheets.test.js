import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/composables/useDensity", () => ({ useDensity: () => ({ isCompact: { value: true } }) }))
vi.mock("@/components/session-v2/model/useSessionDrives", () => ({
  useSessionDrives: () => ({ store: { order: ["d1"] } }),
}))
vi.mock("@/utils/api", () => ({
  terrariumAPI: { startCreature: vi.fn(), stopCreature: vi.fn(), switchCreatureModel: vi.fn() },
}))

import ConversationSheet from "./ConversationSheet.vue"
import PhoneAgentSheet from "./PhoneAgentSheet.vue"
import PhoneHeader from "./PhoneHeader.vue"
import PhoneMenuSheet from "./PhoneMenuSheet.vue"
import PhoneSheet from "./PhoneSheet.vue"
import { mountInSession, phoneChat } from "./phoneHost"
import { terrariumAPI } from "@/utils/api"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
})

describe("PhoneSheet", () => {
  it("closes from the backdrop, the close button and Esc", async () => {
    const w = mount(PhoneSheet, {
      props: { title: "T" },
      slots: { default: "<p>body</p>" },
      global: { stubs: { teleport: true } },
    })
    expect(w.text()).toContain("body")
    await w.find("[data-test='phone-sheet-backdrop']").trigger("click")
    await w.find("[data-test='phone-sheet-close']").trigger("click")
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    const closes = w.emitted("close")
    expect(closes).toHaveLength(3)
    w.unmount()
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    expect(closes).toHaveLength(3)
  })
})

describe("PhoneHeader", () => {
  it("titles the Chat tab with the open conversation and the others' unread; it opens the conversations sheet", async () => {
    const { wrapper, ctx } = mountInSession(PhoneHeader)
    const title = wrapper.find("[data-test='phone-conversation-title']")
    expect(title.text()).toContain("team")
    expect(title.text()).toContain("lead")
    expect(title.text()).toContain("3")
    await title.trigger("click")
    expect(ctx.sheet.value).toEqual({ kind: "conversations", payload: {} })
    await wrapper.find("[data-test='phone-menu-open']").trigger("click")
    expect(ctx.sheet.value.kind).toBe("menu")
  })

  it("titles other tabs with the tab's name", () => {
    const { wrapper } = mountInSession(PhoneHeader, { tab: "status" })
    expect(wrapper.find("[data-test='phone-conversation-title']").exists()).toBe(false)
    expect(wrapper.text()).toContain("Status")
  })
})

describe("ConversationSheet", () => {
  it("lists every agent then every channel; a row opens that conversation in Chat and closes", async () => {
    const { wrapper, ctx, chat } = mountInSession(ConversationSheet, { tab: "status" })
    ctx.openSheet("conversations")
    const keys = wrapper
      .findAll("[data-test^='phone-conv-']")
      .map((b) => b.attributes("data-test").slice(11))
    expect(keys).toEqual(["root", "worker", "ch:tasks"])
    expect(wrapper.find("[data-test='phone-conv-root']").attributes("aria-current")).toBe("true")
    expect(wrapper.find("[data-test='phone-conv-worker']").text()).toContain("codex/gpt-5")
    expect(wrapper.find("[data-test='phone-conv-worker']").text()).toContain("3")
    expect(wrapper.find("[data-test='phone-conv-ch:tasks']").text()).toContain("Work items")
    await wrapper.find("[data-test='phone-conv-ch:tasks']").trigger("click")
    expect(chat.activeTab).toBe("ch:tasks")
    expect(ctx.tab.value).toBe("chat")
    expect(ctx.sheet.value).toBe(null)
  })

  it("adds a creature or a channel through the add dialog", async () => {
    const { wrapper, ctx } = mountInSession(ConversationSheet)
    ctx.openSheet("conversations")
    await wrapper.find("[data-test='phone-add-channel']").trigger("click")
    expect(ctx.addKind.value).toBe("channel")
    expect(ctx.sheet.value).toBe(null)
  })
})

describe("PhoneMenuSheet", () => {
  it("offers the open agent's model, compact and clear through the shown column", async () => {
    const { wrapper, ctx } = mountInSession(PhoneMenuSheet)
    const compact = vi.fn()
    ctx.column.actions.value = { compact, clear: vi.fn() }
    await flushPromises()
    expect(wrapper.find("[data-test='phone-menu-model']").text()).toContain(
      "codex/gpt-6@reasoning=high",
    )
    await wrapper.find("[data-test='phone-menu-model']").trigger("click")
    expect(ctx.sheet.value).toEqual({ kind: "model", payload: { agent: "lead" } })
    ctx.openSheet("menu")
    await wrapper.find("[data-test='phone-menu-compact']").trigger("click")
    expect(compact).toHaveBeenCalledOnce()
    expect(ctx.sheet.value).toBe(null)
  })

  it("has no model row in a channel; tools open widgets or full-screen side views; stop is emitted", async () => {
    const { wrapper, ctx } = mountInSession(PhoneMenuSheet, {
      chat: phoneChat({ activeTab: "ch:tasks" }),
      tab: "chat",
    })
    expect(wrapper.find("[data-test='phone-menu-model']").exists()).toBe(false)
    expect(wrapper.find("[data-test='phone-menu-compact']").attributes("disabled")).toBeDefined()
    expect(wrapper.find("[data-test='phone-tool-drives']").text()).toContain("1")
    expect(wrapper.find("[data-test='phone-tool-graph']").exists()).toBe(false)
    await wrapper.find("[data-test='phone-tool-agents']").trigger("click")
    expect(ctx.widget.value).toBe("agents")
    ctx.setTab("status")
    await wrapper.find("[data-test='phone-tool-terminal']").trigger("click")
    expect(ctx.side.value.kind).toBe("terminal")
    expect(ctx.tab.value).toBe("status")
    await wrapper.find("[data-test='phone-menu-stop']").trigger("click")
    expect(wrapper.findComponent(PhoneMenuSheet).emitted("stop")).toHaveLength(1)
  })
})

describe("PhoneAgentSheet", () => {
  it("shows one agent, stops it, opens its model sheet and its chat", async () => {
    const { wrapper, ctx, chat } = mountInSession(PhoneAgentSheet, {
      props: { name: "worker" },
      tab: "status",
    })
    expect(wrapper.find("[data-test='phone-agent-model']").text()).toContain("codex/gpt-5")
    await wrapper.find("[data-test='phone-agent-stop']").trigger("click")
    await flushPromises()
    expect(terrariumAPI.stopCreature).toHaveBeenCalledWith("g1", "worker")
    await wrapper.find("[data-test='phone-agent-model']").trigger("click")
    expect(ctx.sheet.value).toEqual({ kind: "model", payload: { agent: "worker" } })
    await wrapper.find("[data-test='phone-agent-chat']").trigger("click")
    expect(chat.activeTab).toBe("worker")
    expect(ctx.tab.value).toBe("chat")
  })
})
