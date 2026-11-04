import { createPinia, setActivePinia } from "pinia"
import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"
import { ElMessage } from "element-plus"

import { _resetModelInventoryForTests } from "@/composables/useModelInventory"
import { useAuthStore } from "@/stores/auth"
import { useChatStore } from "@/stores/chat"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useGraphViewStore } from "@/stores/graph/view"
import { useHostsStore } from "@/stores/hosts"
import { configAPI, runtimeGraphAPI, terrariumAPI } from "@/utils/api"
import { fireModelConfigOpen } from "@/utils/layoutEvents"

import CreatureDetails from "./CreatureDetails.vue"

const CURRENT = "codex/current"
const SELECTOR = "codex/other@reasoning=high"
const CANONICAL = "codex/other@reasoning=high,verbosity=low"

function snapshot(model = CURRENT) {
  return {
    graphs: [
      {
        graph_id: "selected-session",
        name: "Selected graph",
        creatures: [
          { creature_id: "immutable-a", name: "worker", llm_name: model, running: true },
          { creature_id: "immutable-b", name: "peer", llm_name: CURRENT, running: true },
        ],
        channels: [],
        output_edges: [],
      },
      {
        graph_id: "chat-session",
        name: "Active chat graph",
        creatures: [
          { creature_id: "chat-creature", name: "worker", llm_name: "codex/chat", running: true },
        ],
        channels: [],
        output_edges: [],
      },
    ],
  }
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

const STUBS = {
  ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
  ElInput: true,
  ElIcon: { template: "<span><slot /></span>" },
  ArrowDown: true,
}

let live, view, hosts, auth, chat, wrappers, switchAPI, snapshotAPI

async function sidebar(creature = null) {
  const initial = creature || view.selected.item
  const Host = defineComponent({
    setup() {
      return () =>
        h(CreatureDetails, {
          creature: creature || view.selected?.item || initial,
          view,
          actions: {},
        })
    },
  })
  const wrapper = mount(Host, { global: { stubs: STUBS } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

function button(wrapper, label) {
  return wrapper.findAll("button").find((item) => item.text().trim() === label)
}

async function choose(wrapper) {
  await wrapper.get(".model-pill").trigger("click")
  await flushPromises()
  await wrapper
    .findAll(".model-row")
    .find((row) => row.text().includes("other"))
    .trigger("click")
  await wrapper
    .findAll(".variation-chip")
    .find((chip) => chip.text() === "high")
    .trigger("click")
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  _resetModelInventoryForTests()
  wrappers = []
  live = useGraphLiveStore()
  live.snapshot = snapshot()
  view = useGraphViewStore("model-picker")
  view.select("creature", "immutable-a")
  hosts = useHostsStore()
  auth = useAuthStore()
  chat = useChatStore("unrelated-chat")
  chat._instanceId = "chat-session"
  chat._instanceGraphId = "chat-session"
  chat.tabs = ["worker"]
  chat.activeTab = "worker"
  chat.modelByTab.worker = { llmName: "codex/chat" }
  vi.spyOn(configAPI, "getModels").mockResolvedValue([
    { provider: "codex", name: "current", model: "current", available: true },
    {
      provider: "codex",
      name: "other",
      model: "other",
      available: true,
      variation_groups: { reasoning: { high: { reasoning_effort: "high" } } },
    },
  ])
  switchAPI = vi.spyOn(terrariumAPI, "switchCreatureModel").mockResolvedValue({ model: CANONICAL })
  snapshotAPI = vi.spyOn(runtimeGraphAPI, "snapshot").mockResolvedValue(snapshot(CANONICAL))
  for (const method of ["success", "error", "warning"])
    vi.spyOn(ElMessage, method).mockImplementation(() => {})
})

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount())
  chat._cleanup()
  vi.restoreAllMocks()
})

describe("CreatureModelPicker in the graph sidebar", () => {
  it("opens the model row and switches only the selected immutable ID with variations and graph readback", async () => {
    const wrapper = await sidebar()
    expect(wrapper.get(".model-pill").text()).toContain(CURRENT)
    await choose(wrapper)
    expect(wrapper.find("code").text()).toBe(SELECTOR)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()

    expect(switchAPI).toHaveBeenCalledExactlyOnceWith("selected-session", "immutable-a", SELECTOR)
    expect(snapshotAPI).toHaveBeenCalledOnce()
    expect(wrapper.get(".model-pill").text()).toContain("codex/other")
    expect(wrapper.get(".model-pill").text()).toContain("reasoning=high, verbosity=low")
    expect(view.model.creatures.find((c) => c.id === "immutable-a").model).toBe(CANONICAL)
    expect(view.model.creatures.find((c) => c.id === "immutable-b").model).toBe(CURRENT)
    expect(chat.modelByTab.worker.llmName).toBe("codex/chat")
    expect(ElMessage.success).toHaveBeenCalledExactlyOnceWith(`Switched to ${CANONICAL}`)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it("cancels a draft without a mutation or graph refresh", async () => {
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Cancel").trigger("click")
    await flushPromises()
    expect(wrapper.find(".model-row").exists()).toBe(false)
    expect(wrapper.get(".model-pill").text()).toContain(CURRENT)
    expect(switchAPI).not.toHaveBeenCalled()
    expect(snapshotAPI).not.toHaveBeenCalled()
  })

  it.each(["sample", "missing creature", "missing session", "unselected"])(
    "disables editing for %s",
    async (reason) => {
      const creature = { ...view.selected.item }
      if (reason === "sample") view.sample = "small"
      if (reason === "missing creature") creature.id = "removed-id"
      if (reason === "missing session") creature.sessionId = ""
      if (reason === "unselected") view.select(null)
      const wrapper = await sidebar(creature)
      expect(wrapper.get(".model-pill").attributes("disabled")).toBeDefined()
      await wrapper.get(".model-pill").trigger("click")
      expect(wrapper.find(".model-row").exists()).toBe(false)
      expect(switchAPI).not.toHaveBeenCalled()
    },
  )

  it("shows a rejected mutation without refreshing or reporting success", async () => {
    switchAPI.mockRejectedValue(new Error("provider unavailable"))
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalledExactlyOnceWith(
      "Model switch failed: provider unavailable",
    )
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(snapshotAPI).not.toHaveBeenCalled()
    expect(wrapper.get(".model-pill").text()).toContain(CURRENT)
    expect(button(wrapper, "Switch").attributes("disabled")).toBeUndefined()
  })

  it("sends only once for rapid Apply clicks", async () => {
    const pending = deferred()
    switchAPI.mockReturnValue(pending.promise)
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await button(wrapper, "Switch").trigger("click")
    expect(switchAPI).toHaveBeenCalledOnce()
    pending.resolve({ model: CANONICAL })
    await flushPromises()
    expect(ElMessage.success).toHaveBeenCalledOnce()
  })

  it.each(["creature", "session"])(
    "discards the open draft when the selected %s changes",
    async (change) => {
      const wrapper = await sidebar()
      await choose(wrapper)
      view.select("creature", change === "creature" ? "immutable-b" : "chat-creature")
      await flushPromises()
      expect(wrapper.find(".model-row").exists()).toBe(false)
      await wrapper.get(".model-pill").trigger("click")
      await flushPromises()
      expect(wrapper.find("code").text()).toBe(change === "creature" ? CURRENT : "codex/current")
      expect(
        wrapper.findAll(".variation-chip").some((chip) => chip.classes().includes("is-active")),
      ).toBe(false)
      expect(switchAPI).not.toHaveBeenCalled()
    },
  )

  it("closes and disables an open draft when its creature disappears", async () => {
    const wrapper = await sidebar()
    await choose(wrapper)
    const removed = snapshot()
    removed.graphs[0].creatures.shift()
    live.snapshot = removed
    await flushPromises()
    expect(wrapper.get(".model-pill").attributes("disabled")).toBeDefined()
    expect(wrapper.find(".model-row").exists()).toBe(false)
    expect(switchAPI).not.toHaveBeenCalled()
  })

  it.each(["host", "user", "host round trip"])(
    "invalidates a draft on a %s runtime change",
    async (change) => {
      const wrapper = await sidebar()
      await choose(wrapper)
      if (change === "user") auth.sameOriginUser = { id: "new-user", username: "new-user" }
      else hosts.activeHostId = "new-host"
      if (change === "host round trip") hosts.activeHostId = null
      await flushPromises()
      expect(wrapper.find(".model-row").exists()).toBe(false)
      expect(wrapper.get(".model-pill").attributes("disabled")).toBeDefined()
      expect(switchAPI).not.toHaveBeenCalled()
    },
  )

  it.each(["selection", "removal", "host", "user", "host round trip", "unmount"])(
    "rejects pending completion after %s changes",
    async (change) => {
      const pending = deferred()
      switchAPI.mockReturnValue(pending.promise)
      const wrapper = await sidebar()
      await choose(wrapper)
      await button(wrapper, "Switch").trigger("click")
      if (change === "selection") view.select("creature", "immutable-b")
      if (change === "removal") live.snapshot = { graphs: [] }
      if (change === "host" || change === "host round trip") hosts.activeHostId = "new-host"
      if (change === "host round trip") hosts.activeHostId = null
      if (change === "user") auth.sameOriginUser = { id: "new-user", username: "new-user" }
      if (change === "unmount") wrapper.unmount()
      pending.resolve({ model: CANONICAL })
      await flushPromises()
      expect(snapshotAPI).not.toHaveBeenCalled()
      expect(ElMessage.success).not.toHaveBeenCalled()
      expect(ElMessage.warning).not.toHaveBeenCalled()
      expect(chat.modelByTab.worker.llmName).toBe("codex/chat")
      if (change === "selection") expect(wrapper.get(".model-pill").text()).toContain(CURRENT)
    },
  )

  it.each(["host", "user", "host round trip"])(
    "preserves the current graph when an old readback finishes after a %s change",
    async (change) => {
      const readback = deferred()
      snapshotAPI.mockReturnValueOnce(readback.promise)
      const wrapper = await sidebar()
      await choose(wrapper)
      await button(wrapper, "Switch").trigger("click")
      await flushPromises()
      expect(snapshotAPI).toHaveBeenCalledOnce()
      if (change === "user") auth.sameOriginUser = { id: "new-user", username: "new-user" }
      else hosts.activeHostId = "new-host"
      if (change === "host round trip") hosts.activeHostId = null
      const currentGraph = snapshot("codex/current-runtime")
      live.handleEvent({ type: "snapshot", snapshot: currentGraph })
      await flushPromises()
      readback.resolve(snapshot(CANONICAL))
      await flushPromises()
      expect(live.snapshot).toEqual(currentGraph)
      expect(wrapper.get(".model-pill").text()).toContain("codex/current-runtime")
      expect(ElMessage.success).not.toHaveBeenCalled()
      expect(ElMessage.warning).not.toHaveBeenCalled()
    },
  )

  it.each(["host", "user", "host round trip"])(
    "opens a fresh draft for the same selected IDs after a %s change",
    async (change) => {
      const wrapper = await sidebar()
      await choose(wrapper)
      if (change === "user") auth.sameOriginUser = { id: "new-user", username: "new-user" }
      else hosts.activeHostId = "new-host"
      if (change === "host round trip") hosts.activeHostId = null
      live.handleEvent({ type: "snapshot", snapshot: snapshot() })
      await flushPromises()
      expect(wrapper.find(".model-row").exists()).toBe(false)
      expect(wrapper.get(".model-pill").attributes("disabled")).toBeUndefined()
      await wrapper.get(".model-pill").trigger("click")
      await flushPromises()
      expect(wrapper.find("code").text()).toBe(CURRENT)
      expect(
        wrapper.findAll(".variation-chip").some((chip) => chip.classes().includes("is-active")),
      ).toBe(false)
      await wrapper
        .findAll(".model-row")
        .find((row) => row.text().includes("other"))
        .trigger("click")
      await button(wrapper, "Switch").trigger("click")
      await flushPromises()
      expect(switchAPI).toHaveBeenCalledExactlyOnceWith(
        "selected-session",
        "immutable-a",
        "codex/other",
      )
      expect(ElMessage.success).toHaveBeenCalledOnce()
    },
  )

  it("warns about failed readback without rejecting an accepted switch", async () => {
    snapshotAPI.mockRejectedValue(new Error("graph offline"))
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(ElMessage.success).toHaveBeenCalledExactlyOnceWith(`Switched to ${CANONICAL}`)
    expect(ElMessage.warning).toHaveBeenCalledOnce()
    expect(ElMessage.warning.mock.calls[0][0]).toContain("graph offline")
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(wrapper.find(".model-row").exists()).toBe(false)
  })

  it("gets a post-switch snapshot even when a pre-switch refresh is already pending", async () => {
    const oldRead = deferred()
    snapshotAPI.mockReturnValueOnce(oldRead.promise)
    const previous = live.refresh()
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    oldRead.resolve(snapshot())
    await previous
    await flushPromises()
    expect(snapshotAPI).toHaveBeenCalledTimes(2)
    expect(wrapper.get(".model-pill").text()).toContain("codex/other")
    expect(wrapper.get(".model-pill").text()).toContain("reasoning=high, verbosity=low")
    expect(ElMessage.success).toHaveBeenCalledOnce()
  })

  it("warns when graph readback remains stale after an accepted switch", async () => {
    snapshotAPI.mockResolvedValue(snapshot())
    const wrapper = await sidebar()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(snapshotAPI).toHaveBeenCalledTimes(2)
    expect(ElMessage.success).toHaveBeenCalledExactlyOnceWith(`Switched to ${CANONICAL}`)
    expect(ElMessage.warning).toHaveBeenCalledOnce()
    expect(ElMessage.warning.mock.calls[0][0]).toContain(CANONICAL)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it("ignores Chat's model-picker shortcut", async () => {
    const wrapper = await sidebar()
    fireModelConfigOpen()
    await flushPromises()
    expect(wrapper.find(".model-row").exists()).toBe(false)
    expect(switchAPI).not.toHaveBeenCalled()
  })
})
