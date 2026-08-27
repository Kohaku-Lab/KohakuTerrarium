import { flushPromises, shallowMount } from "@vue/test-utils"
import { reactive } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

const state = vi.hoisted(() => ({
  detail: null,
  rollup: null,
  timeline: null,
  router: { replace: vi.fn() },
}))

vi.mock("@tanstack/vue-virtual", async () => {
  const { ref } = await import("vue")
  return {
    useVirtualizer: () =>
      ref({
        getVirtualItems: () => [],
        getTotalSize: () => 0,
        measureElement: vi.fn(),
        measure: vi.fn(),
        scrollToIndex: vi.fn(),
      }),
  }
})

vi.mock("vue-router", () => ({
  useRoute: () => reactive({ query: {} }),
  useRouter: () => state.router,
}))

vi.mock("@/stores/sessionDetail", () => ({
  useSessionDetailStore: () => state.detail,
}))
vi.mock("@/stores/turnRollup", () => ({
  useTurnRollupStore: () => state.rollup,
}))
vi.mock("@/stores/traceTimeline", () => ({
  useTraceTimelineStore: () => state.timeline,
}))
vi.mock("@/stores/eventStream", () => ({
  useEventStreamStore: () => ({ appendLive: vi.fn() }),
}))
vi.mock("@/composables/useSessionEventStream", async () => {
  const { ref } = await import("vue")
  return {
    useSessionEventStream: () => ({
      events: ref([]),
      newSinceLastClear: ref(0),
      error: ref(""),
      subscribed: ref(false),
      attach: vi.fn(),
      detach: vi.fn(),
      clearNewCounter: vi.fn(),
    }),
  }
})
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import TraceTab from "./TraceTab.vue"
import TraceEventDetail from "@/components/sessions/trace/TraceEventDetail.vue"
import TraceTimeline from "@/components/sessions/trace/TraceTimeline.vue"
import SubagentConversationPanel from "@/components/subagents/SubagentConversationPanel.vue"

beforeEach(() => {
  state.detail = reactive({
    name: "session-a",
    agents: ["alice"],
    reloadKey: 0,
    summary: { error_turns: [] },
    meta: null,
    live: false,
  })
  state.rollup = reactive({
    sessionName: "session-a",
    agent: "alice",
    aggregate: false,
    turns: [{ turn_index: 1001 }],
    total: 1001,
    loading: false,
    loadingOlder: false,
    hasOlder: true,
    error: "",
    pageError: "",
    load: vi.fn().mockResolvedValue(),
    loadOlder: vi.fn().mockResolvedValue(true),
    ensureTurn: vi.fn().mockResolvedValue(true),
  })
  state.timeline = reactive({
    records: [
      {
        eid: 42,
        turn: 5,
        member: null,
        type: "tool_result",
        label: "bash",
        lane: "tools",
        err: false,
        durMs: 1,
        ts: 1,
      },
    ],
    truncated: false,
    load: vi.fn().mockResolvedValue(),
    appendLive: vi.fn(),
  })
  state.router.replace.mockReset()
})

describe("TraceTab long-session navigation", () => {
  it("opens a sub-agent conversation without changing the trace agent", async () => {
    const wrapper = shallowMount(TraceTab, {
      global: { stubs: { "el-drawer": { template: "<div><slot /></div>" } } },
    })
    await flushPromises()

    const filtersBefore = state.rollup.agent
    const loadCallsBefore = state.rollup.load.mock.calls.length
    wrapper.findComponent(TraceEventDetail).vm.$emit("open-conversation", {
      jobId: "agent_explore_11111111",
      name: "explore",
      run: 2,
      parent: "alice",
    })
    await flushPromises()

    const panel = wrapper.findComponent(SubagentConversationPanel)
    expect(panel.exists()).toBe(true)
    expect(panel.props()).toMatchObject({
      sessionId: "session-a",
      parent: "alice",
      jobId: "agent_explore_11111111",
      name: "explore",
      run: 2,
      live: false,
    })
    expect(state.rollup.agent).toBe(filtersBefore)
    expect(state.rollup.load).toHaveBeenCalledTimes(loadCallsBefore)
  })

  it("uses the runtime graph scope for a store-backed conversation in a live Inspector", async () => {
    state.detail.live = true
    state.detail.meta = { session_id: "creature-123" }
    state.detail.name = "graph-456"
    const wrapper = shallowMount(TraceTab, {
      global: { stubs: { "el-drawer": { template: "<div><slot /></div>" } } },
    })
    await flushPromises()

    wrapper.findComponent(TraceEventDetail).vm.$emit("open-conversation", {
      jobId: "agent_explore_11111111",
      name: "explore",
      run: 2,
      parent: "alice",
    })
    await flushPromises()

    expect(wrapper.findComponent(SubagentConversationPanel).props()).toMatchObject({
      sessionId: "graph-456",
      parent: "alice",
      live: false,
      fill: true,
      showBack: true,
    })

    wrapper.findComponent(SubagentConversationPanel).vm.$emit("back")
    await flushPromises()

    expect(wrapper.findComponent(SubagentConversationPanel).exists()).toBe(false)
    expect(wrapper.findComponent(TraceEventDetail).exists()).toBe(true)
  })

  it("feeds terminal completion ids from turn rollup breakdowns to the detail pane", async () => {
    state.rollup.turns = [
      {
        turn_index: 1001,
        subagent_breakdown: [
          { job_id: "agent_explore_11111111", has_error: false },
          { job_id: "", has_error: false },
        ],
      },
      { turn_index: 1002 },
    ]
    const wrapper = shallowMount(TraceTab, {
      global: { stubs: { "el-drawer": { template: "<div><slot /></div>" } } },
    })
    await flushPromises()

    expect(wrapper.findComponent(TraceEventDetail).props("completedJobIds")).toEqual([
      "agent_explore_11111111",
    ])
  })

  it("resolves a missing turn before routing to a selected timeline span", async () => {
    const wrapper = shallowMount(TraceTab)
    await flushPromises()

    wrapper.findComponent(TraceTimeline).vm.$emit("select-span", {
      turn: 5,
      index: 42,
      member: null,
    })
    await flushPromises()

    expect(state.rollup.ensureTurn).toHaveBeenCalledWith(5)
    expect(state.router.replace).toHaveBeenCalledWith({ query: { turn: 5 } })
  })
})
