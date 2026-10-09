/**
 * Test host for phone components: mounts one component inside a v2 session
 * context built from a plain instance and a reactive chat-store stand-in,
 * with teleports rendered in place. Returns the wrapper and the context.
 */

import { mount } from "@vue/test-utils"
import { defineComponent, h, reactive, ref } from "vue"

import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"

export const PHONE_INSTANCE = {
  id: "g1",
  graph_id: "g1",
  config_name: "team",
  status: "running",
  creatures: [
    { name: "worker", llm_name: "codex/gpt-5", send_channels: ["tasks"] },
    {
      name: "lead",
      is_privileged: true,
      llm_name: "codex/gpt-6@reasoning=high",
      listen_channels: ["tasks"],
    },
  ],
  channels: [{ name: "tasks", description: "Work items" }],
}

export function phoneChat(extra = {}) {
  return reactive({
    _rootSourceName: "lead",
    activeTab: "root",
    tabs: ["root", "worker", "ch:tasks"],
    modelByTab: {},
    processingByTab: {},
    unreadCounts: { worker: 3 },
    tokenUsage: {},
    runningJobs: {},
    openTab: function (key) {
      this.activeTab = key
    },
    ...extra,
  })
}

export function mountInSession(
  component,
  { props = {}, instance = PHONE_INSTANCE, chat = phoneChat(), tab = "chat", stubs = {} } = {},
) {
  let ctx
  const refresh = async () => {}
  const Host = defineComponent({
    setup() {
      ctx = provideSessionV2(
        createSessionV2({
          instance: ref(instance),
          instanceId: ref("g1"),
          chat,
          refresh,
          phone: ref(true),
        }),
      )
      ctx.setTab(tab)
      return () => h(component, props)
    },
  })
  const wrapper = mount(Host, { global: { stubs: { teleport: true, ...stubs } } })
  return { wrapper, ctx, chat }
}
