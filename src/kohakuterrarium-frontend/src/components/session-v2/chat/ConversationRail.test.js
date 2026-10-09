/**
 * ConversationRail busy marks: an agent is busy while it streams OR while its
 * turn waits on its own background jobs; another tab's or an untagged job
 * never marks it busy.
 */

import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"
import { defineComponent, h, reactive, ref } from "vue"

import ConversationRail from "./ConversationRail.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"

function mountRail(chatState) {
  const chat = reactive({
    activeTab: "idle",
    unreadCounts: {},
    processingByTab: {},
    runningJobs: {},
    openTab: () => {},
    ...chatState,
  })
  const instance = ref({
    id: "g1",
    creatures: [
      { name: "tester", running: true },
      { name: "opus", running: true },
      { name: "idle", running: true },
    ],
    channels: [],
  })
  const Host = defineComponent({
    setup() {
      provideSessionV2(createSessionV2({ instance, instanceId: ref("g1"), chat }))
      return () => h(ConversationRail)
    },
  })
  return { wrapper: mount(Host), chat }
}

const spinning = (wrapper, key) =>
  wrapper.find(`[data-test="switch-${key}"]`).find(".i-carbon-circle-dash").exists()

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe("ConversationRail busy marks", () => {
  it("marks an agent busy while its turn waits on its own background jobs", () => {
    const { wrapper } = mountRail({
      processingByTab: { opus: true },
      runningJobs: {
        job_a: { name: "worker", tab: "tester" },
        job_untagged: { name: "bash" },
      },
    })

    expect(spinning(wrapper, "tester")).toBe(true)
    expect(spinning(wrapper, "opus")).toBe(true)
    expect(spinning(wrapper, "idle")).toBe(false)
  })

  it("clears the mark once the agent's background jobs finish", async () => {
    const { wrapper, chat } = mountRail({ runningJobs: { job_a: { tab: "tester" } } })
    expect(spinning(wrapper, "tester")).toBe(true)

    delete chat.runningJobs.job_a
    await wrapper.vm.$nextTick()

    expect(spinning(wrapper, "tester")).toBe(false)
  })

  it("shows the same busy mark in the collapsed rail", async () => {
    localStorage.setItem("kt.v2.rail.collapsed", "1")
    const { wrapper } = mountRail({ runningJobs: { job_a: { tab: "tester" } } })

    expect(spinning(wrapper, "tester")).toBe(true)
    expect(spinning(wrapper, "idle")).toBe(false)
  })
})
