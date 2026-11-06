import { flushPromises, mount } from "@vue/test-utils"
import { expect, it } from "vitest"
import { NewSessionDialog } from "@/ext/sdk/mock"
import { createMockHost } from "./live2dTestHost"
import { ControlSettings } from "../../../../examples/packages/live2d_pet/ui/control-settings.js"
import { settingsFor } from "../../../../examples/packages/live2d_pet/ui/settings-state.js"
import { bridgeFor } from "../../../../examples/packages/live2d_pet/ui/bridge.js"
import { PetBridge } from "../../../../examples/packages/live2d_pet/ui/runtime.js"

const button = (wrapper, text) => wrapper.findAll("button").find((item) => item.text() === text)
const launch = (wrapper) => button(wrapper, "创建 / 打开龙娘对话").trigger("click")

it("explains modes and opens an existing companion or a prefilled creation dialog only on request", async () => {
  let rows = []
  let fail = false
  let pending = null
  const host = createMockHost({
    package: "live2d-pet-demo",
    routes: {
      "GET /companions": () => {
        if (fail) throw new Error("offline")
        return pending || { companions: rows }
      },
    },
  })
  const store = settingsFor(host.kt)
  const bridge = bridgeFor(host.kt)
  const wrapper = mount(ControlSettings, { attachTo: document.body })
  try {
    expect(wrapper.text()).toContain("不增加模型调用")
    expect(wrapper.text()).toContain("沿用原模型")
    expect(wrapper.text()).toContain("同一个 Agent 回复并控制动作")
    expect(wrapper.find("button").exists()).toBe(false)
    store.update({ controlMode: "agent" })
    await flushPromises()
    expect(host.requests).toEqual([])
    await launch(wrapper)
    await flushPromises()
    const dialog = wrapper.findComponent(NewSessionDialog)
    expect(dialog.props()).toMatchObject({
      initialConfig: "@live2d-pet-demo/creatures/companion",
      initialName: "龙娘对话",
      initialAttachMode: "chat",
    })
    expect(host.openedChats).toEqual([])
    dialog.vm.$emit("close")
    await flushPromises()
    expect(wrapper.findComponent(NewSessionDialog).exists()).toBe(false)

    rows = [{ sessionId: "solo", creatureId: "dragon", label: "My dragon" }]
    await launch(wrapper)
    await flushPromises()
    expect(host.openedChats).toEqual(rows)
    expect(host.overlayVisible.dragon).toBe(true)
    expect(wrapper.findComponent(NewSessionDialog).exists()).toBe(false)

    rows.push({ sessionId: "solo", creatureId: "second", label: "Other dragon" })
    await button(wrapper, "更换对话").trigger("click")
    await flushPromises()
    expect(host.openedChats).toHaveLength(1)
    await wrapper.find('select[aria-label="龙娘对话"]').setValue("solo/second")
    await button(wrapper, "打开对话").trigger("click")
    await flushPromises()
    expect(host.openedChats.at(-1)).toEqual(rows[1])
    bridge.connection.value = { ...rows[1], mode: "agent" }
    await flushPromises()
    expect(wrapper.find('[role="status"]').text()).toBe("当前连接：Other dragon")
    await launch(wrapper)
    await flushPromises()
    expect(host.openedChats.at(-1)).toEqual(rows[1])

    fail = true
    await launch(wrapper)
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain("请重试")
    expect(wrapper.findComponent(NewSessionDialog).exists()).toBe(false)
    fail = false
    let finish
    pending = new Promise((resolve) => (finish = resolve))
    const count = host.requests.length
    await launch(wrapper)
    await button(wrapper, "正在打开…").trigger("click")
    expect(host.requests).toHaveLength(count + 1)
    store.update({ controlMode: "pet" })
    await flushPromises()
    finish({ companions: [] })
    await flushPromises()
    expect(wrapper.findComponent(NewSessionDialog).exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  } finally {
    wrapper.unmount()
    bridge.close()
  }
})

it("never presents a pending, stale or rejected binding as a connected conversation", async () => {
  let finish
  const first = new Promise((resolve) => (finish = resolve))
  const host = createMockHost({
    package: "live2d-pet-demo",
    work: { sessionId: "old", creatureId: "old", label: "Old chat", connected: true },
    routes: {
      "POST /binding": ({ data }) => {
        if (!data.enabled) return {}
        if (data.sessionId === "old") return first
        throw Object.assign(new Error("wrong creature"), { response: { status: 409 } })
      },
      "GET /events": () => new Promise(() => {}),
    },
  })
  settingsFor(host.kt).update({ controlMode: "expression" })
  const bridge = bridgeFor(host.kt)
  bridge.renderer(true)
  const controller = mount(PetBridge)
  const settings = mount(ControlSettings)
  try {
    await flushPromises()
    expect(bridge.connection.value).toBeNull()
    expect(settings.text()).not.toContain("当前连接")
    host.state.work = { ...host.state.work, sessionId: "new", creatureId: "new", label: "New chat" }
    await flushPromises()
    finish({ enabled: true })
    await flushPromises()
    expect(bridge.connection.value).toBeNull()
    expect(settings.text()).not.toContain("当前连接")
    expect(settings.text()).toContain("其他页面")
  } finally {
    settings.unmount()
    controller.unmount()
    bridge.close()
  }
})
