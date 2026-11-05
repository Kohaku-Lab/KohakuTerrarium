import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent } from "vue"
import { expect, it } from "vitest"
import { startWorkContextTracking, useWorkContext } from "./workContext"
import { clearAttentionRegistry } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useTabsStore } from "@/stores/tabs"

it("retains work before the consumer mounts, follows live events, and clears closed or foreign bindings", () => {
  setActivePinia(createPinia())
  clearAttentionRegistry()
  const tabs = useTabsStore(),
    hosts = useHostsStore()
  const tracker = startWorkContextTracking()
  const chat = useChatStore("pet-work")
  chat.tabs = ["alice"]
  chat.activeTab = "alice"
  chat.messagesByTab = { alice: [] }
  chat.wsStatus = "open"
  tabs.byId = {
    a: { kind: "attach", target: "pet-work", label: "My agent" },
    preview: { kind: "extension" },
    b: { kind: "attach", target: "other" },
  }
  tabs.activeId = "a"
  tabs.activeId = "preview"
  let retained, focused
  const wrapper = mount(
    defineComponent({
      setup() {
        retained = useWorkContext({ retainLast: true })
        focused = useWorkContext()
        return () => null
      },
    }),
  )
  try {
    expect(focused.value).toBeNull()
    expect(retained.value).toMatchObject({
      sessionId: "pet-work",
      connected: true,
      label: "My agent",
    })
    delete tabs.byId.a.label
    tabs.byId.a.config_name = "Named conversation"
    expect(retained.value.label).toBe("Named conversation")
    chat._onMessage({ type: "processing_start", source: "alice" })
    expect(retained.value).toMatchObject({ status: "running", started: 1 })
    chat.wsStatus = "reconnecting"
    expect(retained.value.connected).toBe(false)
    chat._onMessage({ type: "processing_end", source: "alice" })
    expect(retained.value).toMatchObject({ status: "ready", connected: false })
    chat.wsStatus = "open"
    expect(retained.value.connected).toBe(true)
    tabs.activeId = "b"
    expect(retained.value.sessionId).toBe("other")
    tabs.activeId = "preview"
    delete tabs.byId.b
    expect(retained.value).toBeNull()
    tabs.activeId = "a"
    tabs.activeId = "preview"
    const remote = hosts.addHost({ name: "Remote", url: "https://remote.test" })
    hosts.setActive(remote)
    expect(retained.value).toBeNull()
    hosts.setActive(null)
    expect(retained.value).toBeNull()
    tabs.activeId = "a"
    expect(retained.value.sessionId).toBe("pet-work")
    hosts.setActive(remote)
    tabs.activeId = "preview"
    hosts.setActive(null)
    expect(retained.value).toBeNull()
  } finally {
    wrapper.unmount()
    tracker.stop()
    chat._cleanup()
    clearAttentionRegistry()
  }
})
