import { mount, flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, h } from "vue"
import { createMemoryHistory, createRouter, useRoute, useRouter } from "vue-router"
import { afterEach, expect, it, vi } from "vitest"
import SessionHistoryViewer from "./SessionHistoryViewer.vue"
import { useChatStore } from "@/stores/chat"
import { sessionAPI } from "@/utils/api"
import { useSessionDetailStore } from "@/stores/sessionDetail"

let wrapper
afterEach(() => {
  wrapper?.unmount()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
it("loads saved history through its scoped bounded source and refreshes without dropping older pages", async () => {
  vi.stubGlobal("useRoute", useRoute)
  vi.stubGlobal("useRouter", useRouter)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/sessions/:name", component: SessionHistoryViewer }],
  })
  await router.push("/sessions/saved-one")
  await router.isReady()
  vi.spyOn(sessionAPI, "getHistoryIndex").mockResolvedValue({ meta: {}, targets: ["root"] })
  const full = vi.spyOn(sessionAPI, "getHistory").mockResolvedValue({ messages: [], events: [] })
  const api = vi.spyOn(sessionAPI, "getHistoryPage").mockResolvedValue({
    messages: [{ role: "user", content: "bounded saved", _history_key: "s20" }],
    events: [],
    history_page: {
      version: 1,
      stream: "snapshot",
      history_id: "h1",
      before: "s20",
      after: "s20",
      has_older: true,
      has_newer: false,
      reset_required: false,
    },
    live_job_ids: [],
  })
  const Panel = defineComponent({
    props: ["instance"],
    setup(props) {
      const chat = useChatStore(props.instance.id)
      return () =>
        h(
          "div",
          { "data-test": "transcript" },
          (chat.messagesByTab.root || []).map((m) => m.content).join("|"),
        )
    },
  })
  wrapper = mount(SessionHistoryViewer, {
    attachTo: document.body,
    global: { plugins: [pinia, router], stubs: { ChatPanel: Panel } },
  })
  await flushPromises()
  expect(full).not.toHaveBeenCalled()
  expect(api).toHaveBeenCalledWith("saved-one", "root", expect.objectContaining({ limit: 400 }))
  expect(wrapper.get('[data-test="transcript"]').text()).toBe("bounded saved")
  const chat = useChatStore("session:saved-one")
  expect(chat.historyPageByTab.root.hasOlder).toBe(true)
  expect(chat.runningJobs).toEqual({})
  const metadata = {
    version: 1,
    stream: "snapshot",
    history_id: "h1",
    before: "s10",
    after: "s20",
    has_older: false,
    has_newer: false,
  }
  api.mockResolvedValueOnce({
    messages: [{ role: "user", content: "older", _history_key: "s10" }],
    history_page: metadata,
  })
  await chat.prefetchOlderHistory("root")
  chat.materializeOlderHistory("root")
  await flushPromises()
  expect(wrapper.get('[data-test="transcript"]').text()).toBe("older|bounded saved")
  api.mockResolvedValueOnce({
    messages: [{ role: "user", content: "newer", _history_key: "s30" }],
    history_page: { ...metadata, after: "s30" },
  })
  useSessionDetailStore().reloadKey += 1
  await flushPromises()
  expect(api.mock.calls.at(-1)[2]).toMatchObject({ after: "s20", stream: "snapshot" })
  expect(wrapper.get('[data-test="transcript"]').text()).toBe("older|bounded saved|newer")
  let release
  vi.mocked(sessionAPI.getHistoryIndex).mockImplementationOnce(
    () =>
      new Promise((r) => {
        release = r
      }),
  )
  await router.push("/sessions/delayed")
  await flushPromises()
  await router.push("/sessions/current")
  await flushPromises()
  const current = useChatStore("session:current")
  current.messagesByTab.root = [{ role: "user", content: "keep current" }]
  release({ meta: {}, targets: ["stale"] })
  await flushPromises()
  expect(current.tabs).toEqual(["root"])
  expect(current.messagesByTab.root[0].content).toBe("keep current")
  // A refresh that cannot apply (history changed on disk) must surface.
  api.mockResolvedValueOnce({
    messages: [],
    history_page: { ...metadata, reset_required: true },
  })
  useSessionDetailStore().reloadKey += 1
  await flushPromises()
  expect(wrapper.text()).toContain("History changed on disk")
})

it("re-derives the fallback reasoning panel across a materialized older page", async () => {
  vi.stubGlobal("useRoute", useRoute)
  vi.stubGlobal("useRouter", useRouter)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/sessions/:name", component: SessionHistoryViewer }],
  })
  await router.push("/sessions/reason-one")
  await router.isReady()
  const index = vi
    .spyOn(sessionAPI, "getHistoryIndex")
    .mockResolvedValue({ meta: {}, targets: ["root"] })
  const api = vi.spyOn(sessionAPI, "getHistoryPage").mockResolvedValue({
    messages: [
      { role: "assistant", content: "head", reasoning_content: "head-think", _history_key: "s20" },
    ],
    events: [],
    history_page: {
      version: 1,
      stream: "snapshot",
      history_id: "h1",
      before: "s20",
      after: "s20",
      has_older: true,
      has_newer: false,
      reset_required: false,
    },
    live_job_ids: [],
  })
  const Panel = defineComponent({
    props: ["instance"],
    setup(props) {
      return () => h("div", { "data-test": "transcript" })
    },
  })
  wrapper = mount(SessionHistoryViewer, {
    attachTo: document.body,
    global: { plugins: [pinia, router], stubs: { ChatPanel: Panel } },
  })
  await flushPromises()
  expect(index).toHaveBeenCalledWith("reason-one")
  // Snapshot head reasoning surfaces in the fallback panel.
  expect(wrapper.text()).toContain("chain-of-thought (1)")
  const reasoningToggle = wrapper
    .findAll("button")
    .find((b) => b.text().includes("chain-of-thought"))
  await reasoningToggle.trigger("click")
  expect(wrapper.text()).toContain("head-think")
  const chat = useChatStore("session:reason-one")
  const olderPage = {
    messages: [
      {
        role: "assistant",
        content: "older",
        reasoning_content: "older-think",
        _history_key: "s10",
      },
    ],
    events: [],
    history_page: {
      version: 1,
      stream: "snapshot",
      history_id: "h1",
      before: "s10",
      after: "s20",
      has_older: false,
      has_newer: false,
      reset_required: false,
    },
    live_job_ids: [],
  }
  api.mockResolvedValueOnce(olderPage)
  await chat.prefetchOlderHistory("root")
  chat.materializeOlderHistory("root")
  await flushPromises()
  // Materializing the older page must surface the older reasoning in the
  // reactive fallback panel instead of leaving it stale at the head count.
  expect(wrapper.text()).toContain("chain-of-thought (2)")
  expect(wrapper.text()).toContain("older-think")
})
