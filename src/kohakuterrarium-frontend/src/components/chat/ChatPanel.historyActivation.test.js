import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import ChatPanelContainer from "./ChatPanelContainer.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const names = ["alice", "bob", "carol"]
const instance = {
  id: "activation",
  graph_id: "activation",
  type: "terrarium",
  creatures: names.map((name) => ({ name, status: "idle" })),
}
const stubs = {
  ChatMessage: { props: ["message"], template: "<div>{{ message.content }}</div>" },
  ModelSwitcher: true,
  SiteChip: true,
  StatusDot: true,
}
class Socket {
  static OPEN = 1
  readyState = 1
  close() {}
  send() {}
}
function page(target, { empty = false, stream = "events" } = {}) {
  return {
    events: empty
      ? []
      : [{ type: "user_message", event_id: 1, content: `${target} history`, _history_key: "e:1" }],
    messages: [],
    live_job_ids: [],
    is_processing: false,
    history_page: {
      version: 1,
      stream,
      history_id: target,
      before: empty ? null : "b1",
      after: empty ? null : "a1",
      has_older: false,
      has_newer: false,
      reset_required: false,
    },
  }
}
let chat, wrapper, api
const callsFor = (name) => api.mock.calls.filter(([, target]) => target === name)
async function connect() {
  chat._connectTerrarium(instance.id, chat._instanceGeneration)
  chat._ws.onopen()
  await vi.waitFor(() => expect(chat._historyLoaded).toBe(true))
  await flushPromises()
}
async function mountPanels(props = {}) {
  wrapper = mount(ChatPanelContainer, {
    attachTo: document.body,
    props: { instance, ...props },
    global: { stubs },
  })
  await flushPromises()
}
async function clickTab(name) {
  const tab = wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes(name))
  await tab.trigger("click")
  await flushPromises()
}
beforeEach(() => {
  const storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  })
  vi.stubGlobal("WebSocket", Socket)
  setActivePinia(createPinia())
  chat = useChatStore(instance.id)
  chat._instanceId = instance.id
  chat._instanceGraphId = instance.graph_id
  chat._instanceType = instance.type
  for (const name of names) {
    chat._addTab(name)
    chat.commandInventoryByTab[name] = { commands: [], skills: [] }
    chat._commandInventoryFetchedAtByTab[name] = Date.now()
  }
  chat.activeTab = "alice"
  api = vi
    .spyOn(terrariumAPI, "getHistoryPage")
    .mockImplementation(async (_, target) => page(target))
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  chat._cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("loads the clicked creature once without changing another creature's transcript", async () => {
  await connect()
  await mountPanels()
  const alice = chat.messagesByTab.alice
  await clickTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  expect(callsFor("bob")).toHaveLength(1)
  expect(chat.messagesByTab.alice).toBe(alice)
  await clickTab("carol")
  await vi.waitFor(() => expect(wrapper.text()).toContain("carol history"))
  await clickTab("bob")
  await clickTab("bob")
  expect(callsFor("bob")).toHaveLength(1)
  expect(callsFor("carol")).toHaveLength(1)
})

it("loads visible restored splits after bootstrap, leaving hidden tabs unloaded", async () => {
  const first = chat.enableGroups()
  chat.splitGroup(first, "horizontal", "after", "bob")
  chat.setFocusedGroup(first)
  chat.groups = {}
  chat.groupTree = null
  chat.focusedGroupId = null
  await mountPanels()
  expect(api).not.toHaveBeenCalled()
  await connect()
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  expect(wrapper.text()).toContain("alice history")
  expect(callsFor("alice")).toHaveLength(1)
  expect(callsFor("bob")).toHaveLength(1)
  expect(callsFor("carol")).toHaveLength(0)
})

it("loads a tab made visible by splitting or moving without a tab click", async () => {
  await connect()
  await mountPanels()
  const first = chat.focusedGroupId
  const second = chat.splitGroup(first, "horizontal", "after", "bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  chat.moveTab(first, "carol", second)
  await vi.waitFor(() => expect(wrapper.text()).toContain("carol history"))
  expect(callsFor("bob")).toHaveLength(1)
  expect(callsFor("carol")).toHaveLength(1)
})

it("shares pending activation with other mounted views and repeated switches", async () => {
  await connect()
  let release
  api.mockImplementation((_, target) =>
    target === "bob"
      ? new Promise((resolve) => {
          release = resolve
        })
      : Promise.resolve(page(target)),
  )
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(release).toBeTypeOf("function"))
  const sibling = mount(ChatPanel, {
    props: { instance, groupId: chat.focusedGroupId },
    global: { provide: { chatStore: chat }, stubs },
  })
  try {
    await clickTab("alice")
    await clickTab("bob")
    expect(callsFor("bob")).toHaveLength(1)
    release(page("bob"))
    await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  } finally {
    sibling.unmount()
  }
})

it.each(["paged", "legacy"])("remembers a successful empty %s history", async (kind) => {
  await connect()
  api.mockImplementation(async (_, target, options) =>
    kind === "legacy"
      ? { events: [], messages: [] }
      : page(target, { empty: true, stream: options.stream || "events" }),
  )
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(callsFor("bob").length).toBe(kind === "paged" ? 2 : 1))
  await flushPromises()
  await clickTab("alice")
  await clickTab("bob")
  expect(callsFor("bob")).toHaveLength(kind === "paged" ? 2 : 1)
  expect(chat.messagesByTab.bob).toEqual([])
})

it("retries a failed activation on re-entry without an automatic request loop", async () => {
  await connect()
  vi.spyOn(console, "error").mockImplementation(() => {})
  api.mockRejectedValueOnce(new Error("offline"))
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(callsFor("bob")).toHaveLength(1))
  await flushPromises()
  expect(callsFor("bob")).toHaveLength(1)
  await clickTab("alice")
  await clickTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  expect(callsFor("bob")).toHaveLength(2)
})

it("does not initiate live history from a read-only panel", async () => {
  await connect()
  await mountPanels({ readOnly: true })
  api.mockClear()
  await clickTab("bob")
  expect(api).not.toHaveBeenCalled()
})

it.each(["close", "instance", "mutation"])(
  "discards a delayed activation after %s changes",
  async (change) => {
    await connect()
    let release
    api.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    await mountPanels()
    await clickTab("bob")
    await vi.waitFor(() => expect(release).toBeTypeOf("function"))
    if (change === "close") chat.closeTab("bob")
    else if (change === "instance") chat.unbindFromInstance()
    else chat._onMessage({ type: "text", source: "bob", content: "new live text" })
    await nextTick()
    const current = chat.messagesByTab.bob
    release(page("bob"))
    await flushPromises()
    expect(chat.messagesByTab.bob).toBe(current)
    expect(wrapper.text()).not.toContain("bob history")
    if (change === "mutation") expect(JSON.stringify(current)).toContain("new live text")
  },
)

it("keeps explicit reconnect refreshes for a previously loaded tab", async () => {
  await connect()
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  api.mockImplementation(async (_, target) => ({
    ...page(target),
    events: [
      { type: "user_message", event_id: 2, content: `${target} newer`, _history_key: "e:2" },
    ],
  }))
  await connect()
  expect(wrapper.text()).toContain("bob newer")
  expect(callsFor("bob")).toHaveLength(2)
})

it.each(["openTab", "setActiveTab", "initForInstance"])(
  "shares %s activation with the visible panel",
  async (entry) => {
    await connect()
    await mountPanels()
    if (entry === "initForInstance") chat.initForInstance(instance, { initialTab: "bob" })
    else chat[entry]("bob")
    await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
    expect(callsFor("bob")).toHaveLength(1)
  },
)

it("loads history even if a live frame already created a message in the hidden tab", async () => {
  await connect()
  chat._onMessage({ type: "text", source: "bob", content: "live frame" })
  api.mockImplementation(async (_, target) => ({
    ...page(target),
    is_processing: target === "bob",
  }))
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  expect(callsFor("bob")).toHaveLength(1)
  expect(chat.processingByTab.bob).toBe(true)
})

it("loads a reopened tab after disposing its previous history controller", async () => {
  await connect()
  await mountPanels()
  await clickTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  chat.closeTab("bob")
  await flushPromises()
  chat.openTab("bob")
  await vi.waitFor(() => expect(wrapper.text()).toContain("bob history"))
  expect(callsFor("bob")).toHaveLength(2)
})
