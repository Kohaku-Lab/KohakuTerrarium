import { createPinia, setActivePinia } from "pinia"
import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ElMessage } from "element-plus"
import { _resetModelInventoryForTests } from "@/composables/useModelInventory"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { configAPI, sessionAPI, terrariumAPI } from "@/utils/api"
import ModelSwitcher from "./ModelSwitcher.vue"
vi.mock("vue-router", () => ({ useRoute: () => ({ params: {} }) }))
function deferred() {
  let resolve
  const promise = new Promise((yes) => {
    resolve = yes
  })
  return { promise, resolve }
}
function detail(id = "a", names = ["alice"], hasRoot = false) {
  return {
    session_id: id,
    name: id,
    has_root: hasRoot,
    creatures: names.map((name, i) => ({
      name,
      is_root: hasRoot && i === 0,
      model: "current",
      llm_name: "codex/current",
    })),
    channels: [],
  }
}
const summary = (id = "a", count = 1) => ({ session_id: id, name: id, creatures: count })
let store, chat, hosts, wrappers, switchAPI, detailAPI
function button(wrapper, label) {
  return wrapper.findAll("button").find((item) => item.text().trim() === label)
}
async function picker() {
  const wrapper = mount(ModelSwitcher, {
    props: { instanceId: "a" },
    global: {
      provide: { "kt:scope": "a" },
      stubs: {
        ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
        ElInput: true,
        ElOption: true,
        ElSelect: true,
        ElButton: {
          emits: ["click"],
          template: "<button @click=\"$emit('click')\"><slot /></button>",
        },
        ElIcon: { template: "<span><slot /></span>" },
        ArrowDown: true,
      },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function choose(wrapper) {
  await wrapper.find(".model-pill").trigger("click")
  await flushPromises()
  await wrapper
    .findAll(".model-row")
    .find((row) => row.text().includes("other"))
    .trigger("click")
}
beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  _resetModelInventoryForTests()
  wrappers = []
  store = useInstancesStore()
  hosts = useHostsStore()
  chat = useChatStore("a")
  chat._instanceId = "a"
  chat._instanceGraphId = "a"
  chat.tabs = ["alice"]
  chat.activeTab = "alice"
  chat.sessionInfo.llmName = "codex/current"
  chat.modelByTab.alice = { model: "current", llmName: "codex/current" }
  vi.spyOn(configAPI, "getModels").mockResolvedValue([
    { provider: "codex", name: "current", model: "current", available: true },
    { provider: "codex", name: "other", model: "other", available: true },
  ])
  vi.spyOn(sessionAPI, "listActive").mockResolvedValue([summary(), summary("b")])
  detailAPI = vi
    .spyOn(sessionAPI, "getActive")
    .mockImplementation(async (id) => detail(id, id === "a" ? ["alice"] : ["bob"]))
  switchAPI = vi
    .spyOn(terrariumAPI, "switchCreatureModel")
    .mockResolvedValue({ model: "codex/other" })
  for (const method of ["success", "error", "warning"])
    vi.spyOn(ElMessage, method).mockImplementation(() => {})
})
afterEach(() => {
  wrappers.forEach((w) => w.unmount())
  chat._cleanup()
  vi.restoreAllMocks()
})
describe("model switch target ownership with real session stores", () => {
  it("switches in one click after another tab's detail and a summary refresh", async () => {
    await store.fetchOne("a")
    const wrapper = await picker()
    await choose(wrapper)
    await store.fetchOne("b")
    await store.fetchAll()
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "alice", "codex/other")
    expect(chat.modelByTab.alice.llmName).toBe("codex/other")
    expect(ElMessage.error).not.toHaveBeenCalled()
  })
  it("keeps a summary-only solo picker disabled until detail arrives", async () => {
    await store.fetchAll()
    const wrapper = await picker()
    expect(wrapper.find(".model-pill").attributes("disabled")).toBeDefined()
    await store.fetchOne("a")
    await flushPromises()
    expect(wrapper.find(".model-pill").attributes("disabled")).toBeUndefined()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "alice", "codex/other")
  })
  it("does not submit an open drawer while a changed count awaits detail", async () => {
    await store.fetchOne("a")
    const wrapper = await picker()
    await choose(wrapper)
    sessionAPI.listActive.mockResolvedValue([summary("a", 2)])
    await store.fetchAll()
    await flushPromises()
    expect(button(wrapper, "Switch").attributes("disabled")).toBeDefined()
    await button(wrapper, "Switch").trigger("click")
    expect(switchAPI).not.toHaveBeenCalled()
    detailAPI.mockResolvedValue(detail("a", ["alice", "beta"]))
    await store.fetchOne("a")
    await flushPromises()
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "alice", "codex/other")
  })
  it("does not replace a removed selected creature with the remaining solo creature", async () => {
    detailAPI.mockResolvedValue(detail("a", ["alice", "beta"]))
    await store.fetchOne("a")
    const wrapper = await picker()
    await choose(wrapper)
    detailAPI.mockResolvedValue(detail("a", ["beta"]))
    await store.fetchOne("a")
    await flushPromises()
    expect(button(wrapper, "Switch").attributes("disabled")).toBeDefined()
    await button(wrapper, "Switch").trigger("click")
    expect(switchAPI).not.toHaveBeenCalled()
  })
  it.each(["alice", "root", "ch:news"])(
    "resolves target %s without silently choosing another member",
    async (active) => {
      detailAPI.mockResolvedValue(detail("a", ["leader", "alice"], true))
      await store.fetchOne("a")
      chat.activeTab = active
      chat.tabs = ["root", "alice", "ch:news"]
      const wrapper = await picker()
      if (active.startsWith("ch:")) {
        expect(wrapper.find(".model-pill").attributes("disabled")).toBeDefined()
        expect(switchAPI).not.toHaveBeenCalled()
        return
      }
      await choose(wrapper)
      await button(wrapper, "Switch").trigger("click")
      await flushPromises()
      expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", active, "codex/other")
      expect(chat.modelByTab[active].llmName).toBe("codex/other")
      if (active === "root") expect(chat.modelByTab.leader.llmName).toBe("codex/other")
    },
  )
  it("rejects duplicate submissions while a switch is pending", async () => {
    await store.fetchOne("a")
    const pending = deferred()
    switchAPI.mockReturnValue(pending.promise)
    const wrapper = await picker()
    await choose(wrapper)
    await button(wrapper, "Switch").trigger("click")
    await button(wrapper, "Switch").trigger("click")
    expect(switchAPI).toHaveBeenCalledTimes(1)
    pending.resolve({ model: "codex/other" })
    await flushPromises()
  })
  it.each(["host", "session", "stop", "unmount"])(
    "does not apply a late switch result after %s changes",
    async (change) => {
      await store.fetchOne("a")
      const pending = deferred()
      switchAPI.mockReturnValue(pending.promise)
      const wrapper = await picker()
      await choose(wrapper)
      await button(wrapper, "Switch").trigger("click")
      const reads = detailAPI.mock.calls.length
      if (change === "host") hosts.activeHostId = "other-host"
      if (change === "session") {
        chat._instanceGeneration++
        chat._instanceId = "b"
        chat._instanceGraphId = "b"
      }
      if (change === "stop") store.markRuntimeStopped("a")
      if (change === "unmount") wrapper.unmount()
      pending.resolve({ model: "codex/other" })
      await flushPromises()
      expect(detailAPI).toHaveBeenCalledTimes(reads)
      expect(chat.modelByTab.alice.llmName).toBe("codex/current")
      expect(ElMessage.success).not.toHaveBeenCalled()
    },
  )
  it("keeps the accepted switch successful when metadata readback fails", async () => {
    await store.fetchOne("a")
    const wrapper = await picker()
    await choose(wrapper)
    detailAPI.mockRejectedValue(new Error("offline"))
    vi.spyOn(console, "error").mockImplementation(() => {})
    await button(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(chat.modelByTab.alice.llmName).toBe("codex/other")
    expect(ElMessage.success).toHaveBeenCalledOnce()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledOnce()
  })
})

it("does not report an old host's switch as success after its metadata readback finishes", async () => {
  await store.fetchOne("a")
  const wrapper = await picker()
  await choose(wrapper)
  const pending = deferred()
  detailAPI.mockReturnValue(pending.promise)
  await button(wrapper, "Switch").trigger("click")
  await flushPromises()
  expect(chat.modelByTab.alice.llmName).toBe("codex/other")
  hosts.activeHostId = "other-host"
  chat.modelByTab.alice = { llmName: "other-host/model" }
  pending.resolve(detail())
  await flushPromises()
  expect(chat.modelByTab.alice.llmName).toBe("other-host/model")
  expect(store.current).toBeNull()
  expect(ElMessage.success).not.toHaveBeenCalled()
})

it("rejects a switch result after leaving and returning to the same host in one tick", async () => {
  await store.fetchOne("a")
  const wrapper = await picker()
  await choose(wrapper)
  const pending = deferred()
  switchAPI.mockReturnValue(pending.promise)
  await button(wrapper, "Switch").trigger("click")
  hosts.activeHostId = "other-host"
  hosts.activeHostId = null
  pending.resolve({ model: "codex/other" })
  await flushPromises()
  expect(chat.modelByTab.alice.llmName).toBe("codex/current")
  expect(ElMessage.success).not.toHaveBeenCalled()
})

it("does not reject a valid switch when an unrelated session stops", async () => {
  await store.fetchOne("a")
  await store.fetchOne("b")
  const wrapper = await picker()
  await choose(wrapper)
  const pending = deferred()
  switchAPI.mockReturnValue(pending.promise)
  await button(wrapper, "Switch").trigger("click")
  store.markRuntimeStopped("b")
  pending.resolve({ model: "codex/other" })
  await flushPromises()
  expect(chat.modelByTab.alice.llmName).toBe("codex/other")
  expect(ElMessage.success).toHaveBeenCalledOnce()
  expect(ElMessage.error).not.toHaveBeenCalled()
})

it("retains the target across repeated tab unmounts and summary polls", async () => {
  await store.fetchOne("a")
  for (let i = 0; i < 5; i++) {
    const previous = await picker()
    previous.unmount()
    await store.fetchOne("b")
    await store.fetchAll()
  }
  const wrapper = await picker()
  await choose(wrapper)
  await button(wrapper, "Switch").trigger("click")
  await flushPromises()
  expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "alice", "codex/other")
  expect(ElMessage.error).not.toHaveBeenCalled()
})

it("does not apply a pending switch to the newly selected member", async () => {
  detailAPI.mockResolvedValue(detail("a", ["alice", "beta"]))
  await store.fetchOne("a")
  chat.tabs = ["alice", "beta"]
  chat.modelByTab.beta = { llmName: "codex/current" }
  const wrapper = await picker()
  await choose(wrapper)
  const pending = deferred()
  switchAPI.mockReturnValue(pending.promise)
  await button(wrapper, "Switch").trigger("click")
  chat.setActiveTab("beta")
  pending.resolve({ model: "codex/other" })
  await flushPromises()
  expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "alice", "codex/other")
  expect(chat.modelByTab.beta.llmName).toBe("codex/current")
  expect(ElMessage.success).not.toHaveBeenCalled()
})

it("switches a standalone creature literally named root without a privileged alias", async () => {
  detailAPI.mockResolvedValue(detail("a", ["root"]))
  await store.fetchOne("a")
  chat.tabs = ["root"]
  chat.activeTab = "root"
  const wrapper = await picker()
  expect(wrapper.find(".model-pill").attributes("disabled")).toBeUndefined()
  await choose(wrapper)
  await button(wrapper, "Switch").trigger("click")
  await flushPromises()
  expect(switchAPI).toHaveBeenCalledExactlyOnceWith("a", "root", "codex/other")
})
