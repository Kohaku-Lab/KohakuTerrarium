import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { computed, defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import CompactWorkspaceShell from "./CompactWorkspaceShell.vue"
import { useLayoutStore } from "@/stores/layout"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"
import { LAYOUT_EVENTS, fireOpenDrivesDrawer, onLayoutEvent } from "@/utils/layoutEvents"

function panel(name) {
  return defineComponent({
    name,
    props: { instance: { type: Object, default: null } },
    render() {
      return h("div", { "data-panel": name }, this.instance?.id ?? "no-instance")
    },
  })
}

const instance = { id: "graph_1" }

function mountShell() {
  const layout = useLayoutStore("test-scope")
  layout.registerPanel({ id: "chat", label: "Chat", component: panel("chat") })
  layout.registerPanel({ id: "state", label: "State", component: panel("state") })
  layout.registerPanel({ id: "drives", label: "Drives", component: panel("drives") })
  layout.registerBuiltinPreset({
    id: "p",
    label: "P",
    zones: { main: { visible: true, size: 100 } },
    slots: [
      { zoneId: "main", panelId: "chat" },
      { zoneId: "main", panelId: "state" },
    ],
  })
  layout.switchPreset("p")
  const panelProps = computed(() => ({
    chat: { instance },
    state: { instance },
    drives: { instance },
  }))
  const wrapper = mount(CompactWorkspaceShell, {
    props: { instanceId: "graph_1" },
    global: { provide: { panelProps, "kt:scope": "test-scope" } },
  })
  mounted.push(wrapper)
  return { wrapper, layout }
}

const mounted = []

describe("CompactWorkspaceShell", () => {
  beforeEach(() => {
    _resetUIPrefsForTests()
    setActivePinia(createPinia())
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
    })
  })

  afterEach(() => {
    while (mounted.length) mounted.pop().unmount()
    vi.unstubAllGlobals()
  })

  it("opens the Drives panel when the state panel asks and nothing else claims it", async () => {
    const { wrapper } = mountShell()
    await flushPromises()
    expect(wrapper.find("[data-panel='chat']").exists()).toBe(true)

    const claimed = fireOpenDrivesDrawer({ sessionId: "graph_1" })
    await flushPromises()

    expect(claimed).toBe(true)
    expect(wrapper.find("[data-panel='drives']").exists()).toBe(true)
  })

  it("hands the deep-linked drive to the Drives panel once it is showing", async () => {
    const { wrapper } = mountShell()
    await flushPromises()
    const seen = []
    const off = onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES, (evt) => {
      seen.push({ detail: evt.detail, drivesShown: wrapper.find("[data-panel='drives']").exists() })
    })

    fireOpenDrivesDrawer({ sessionId: "graph_1", driveId: "d1" })
    await flushPromises()
    off()

    expect(seen).toEqual([{ detail: { sessionId: "graph_1", driveId: "d1" }, drivesShown: true }])
  })

  it("ignores a request for a different session", async () => {
    const { wrapper } = mountShell()
    await flushPromises()

    const claimed = fireOpenDrivesDrawer({ sessionId: "other" })
    await flushPromises()

    expect(claimed).toBe(false)
    expect(wrapper.find("[data-panel='chat']").exists()).toBe(true)
  })
})
