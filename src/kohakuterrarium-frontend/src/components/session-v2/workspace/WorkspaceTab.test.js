import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ref } from "vue"

vi.mock("@/components/session-v2/model/sessionContext", () => ({
  useSessionV2: () => ({ instanceId: ref("inst-1") }),
}))
vi.mock("@/components/session-v2/model/v2Strings", () => ({ useV2T: () => (k) => k }))

import WorkspaceTab from "./WorkspaceTab.vue"

const stubs = {
  FileColumn: { template: "<div data-test='file-column' />" },
  EditorColumn: {
    template: "<div data-test='editor-column' />",
    methods: { openFile() {}, showDiff() {} },
  },
  ChatColumn: { template: "<div data-test='chat-column' />" },
}

let wrapper
beforeEach(() => localStorage.clear())
afterEach(() => wrapper?.unmount())

async function dragGrip(name, rect, toX) {
  const grip = wrapper.find(`[data-test='v2-ws-${name}-grip']`)
  grip.element.parentElement.getBoundingClientRect = () => rect
  await grip.trigger("pointerdown", { pointerId: 1 })
  document.dispatchEvent(new MouseEvent("pointermove", { clientX: toX }))
  document.dispatchEvent(new MouseEvent("pointerup"))
  await flushPromises()
}

describe("WorkspaceTab", () => {
  it("resizes the file column and the chat column by their grips and keeps the widths for every session", async () => {
    wrapper = mount(WorkspaceTab, { global: { stubs } })
    const files = () => wrapper.find("[data-test='v2-ws-files']")
    const chat = () => wrapper.find("[data-test='v2-ws-chat']")
    expect(files().attributes("style")).toContain("width: 256px")
    expect(chat().attributes("style")).toContain("width: 416px")
    await dragGrip("files", { left: 0, right: 256 }, 340)
    expect(files().attributes("style")).toContain("width: 340px")
    await dragGrip("chat", { left: 900, right: 1316 }, 800)
    expect(chat().attributes("style")).toContain("width: 516px")
    expect(localStorage.getItem("kt.v2.ws.filesWidth")).toBe("340")
    expect(localStorage.getItem("kt.v2.ws.chatWidth")).toBe("516")
    await wrapper.find("[data-test='v2-ws-chat-grip']").trigger("dblclick")
    expect(chat().attributes("style")).toContain("width: 416px")
  })

  it("collapses files and chat per session and drops their grips while collapsed", async () => {
    localStorage.setItem("kt.v2.ws.inst-1.chat", "0")
    wrapper = mount(WorkspaceTab, { global: { stubs } })
    expect(wrapper.find("[data-test='v2-ws-chat']").exists()).toBe(false)
    expect(wrapper.find("[data-test='v2-ws-chat-grip']").exists()).toBe(false)
    await wrapper.find("[data-test='v2-chat-expand']").trigger("click")
    expect(wrapper.find("[data-test='v2-ws-chat-grip']").exists()).toBe(true)
    expect(localStorage.getItem("kt.v2.ws.inst-1.chat")).toBe("1")
  })
})
