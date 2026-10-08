import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const createSession = vi.hoisted(() => vi.fn())
const openTab = vi.hoisted(() => vi.fn())
const cluster = vi.hoisted(() => ({ isCluster: false }))

vi.mock("@/utils/api", () => ({
  configAPI: {
    getServerInfo: vi.fn(),
    listCreatures: vi.fn(),
    listTerrariums: vi.fn(),
  },
}))
vi.mock("@/stores/tabs", () => ({ useTabsStore: () => ({ createSession, openTab }) }))
vi.mock("@/stores/cluster", () => ({ useClusterStore: () => cluster }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))
vi.mock("@/utils/randomName", () => ({ randomNameFor: (kind) => `random-${kind}` }))
vi.mock("@/components/common/DirectoryPickerDialog.vue", () => ({
  default: { name: "DirectoryPickerDialog", template: "<div />" },
}))
vi.mock("@/components/cluster/SitePicker.vue", () => ({
  default: {
    name: "SitePicker",
    props: ["modelValue", "label", "executionTarget"],
    emits: ["update:modelValue"],
    template: `<select data-testid="site" :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
      <option value="">none</option><option value="_host">host</option><option value="worker-1">worker-1</option>
    </select>`,
  },
}))

import { configAPI } from "@/utils/api"
import NewSessionDialog from "./NewSessionDialog.vue"

const row = (name, kind = "creatures") => ({ name, path: `@pkg/${kind}/${name}` })
const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const submit = (w) => w.find('[data-test="new-submit"]')
const pwdInput = (w) => w.find('[data-test="new-pwd-input"]')
const configInput = (w) => w.find('[data-test="new-config-path-input"]')

beforeEach(() => {
  setActivePinia(createPinia())
  cluster.isCluster = false
  configAPI.getServerInfo.mockReset().mockResolvedValue({ cwd: "/host" })
  configAPI.listCreatures.mockReset().mockResolvedValue([row("general")])
  configAPI.listTerrariums.mockReset().mockResolvedValue([row("swe_team", "terrariums")])
  createSession.mockReset().mockResolvedValue("sid")
  openTab.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ""
})

describe("NewSessionDialog: creature", () => {
  it("starts a catalog creature on the default machine in its working directory", async () => {
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    expect(configAPI.listCreatures).toHaveBeenCalledWith({ onNode: "_host" })
    expect(configAPI.getServerInfo).toHaveBeenCalledWith({ onNode: "_host" })
    expect(pwdInput(w).element.value).toBe("/host")
    expect(submit(w).element.disabled).toBe(true)
    await w.find('[data-test="add-config-general"]').trigger("click")
    expect(submit(w).element.disabled).toBe(false)
    await submit(w).trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith({
      kind: "creature",
      configPath: "@pkg/creatures/general",
      pwd: "/host",
      name: "random-creature",
      attachMode: "chat",
      onNode: "_host",
    })
    expect(w.emitted("close")).toHaveLength(1)
    w.unmount()
  })

  it("starts from a typed local path and the chosen name and open mode", async () => {
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    await configInput(w).setValue("/my/creature")
    await w.find('[data-test="new-name"]').setValue("alice")
    await w.find('[data-test="new-open-both"]').trigger("click")
    await submit(w).trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith(
      expect.objectContaining({ configPath: "/my/creature", name: "alice", attachMode: "both" }),
    )
    w.unmount()
  })

  it("re-reads the catalog and directory of a new machine and drops the old choice", async () => {
    configAPI.listCreatures
      .mockResolvedValueOnce([row("host-only")])
      .mockResolvedValueOnce([row("worker-only")])
    configAPI.getServerInfo
      .mockResolvedValueOnce({ cwd: "/host" })
      .mockResolvedValueOnce({ cwd: "/worker" })
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    await w.find('[data-test="add-config-host-only"]').trigger("click")
    await w.find('[data-testid="site"]').setValue("worker-1")
    expect(submit(w).element.disabled).toBe(true)
    await flushPromises()
    expect(configAPI.listCreatures).toHaveBeenLastCalledWith({ onNode: "worker-1" })
    expect(configAPI.getServerInfo).toHaveBeenLastCalledWith({ onNode: "worker-1" })
    expect(w.text()).toContain("worker-only")
    expect(w.text()).not.toContain("host-only")
    expect(pwdInput(w).element.value).toBe("/worker")
    expect(configInput(w).element.value).toBe("")
    w.unmount()
  })

  it.each(["success", "failure"])(
    "ignores an older machine's catalog %s and directory",
    async (outcome) => {
      const oldCatalog = deferred()
      const oldDirectory = deferred()
      configAPI.listCreatures
        .mockReturnValueOnce(oldCatalog.promise)
        .mockResolvedValueOnce([row("worker-only")])
      configAPI.getServerInfo
        .mockReturnValueOnce(oldDirectory.promise)
        .mockResolvedValueOnce({ cwd: "/worker" })
      const w = mount(NewSessionDialog, { attachTo: document.body })
      await w.find('[data-testid="site"]').setValue("worker-1")
      await flushPromises()
      if (outcome === "success") oldCatalog.resolve([row("stale-host")])
      else oldCatalog.reject(new Error("stale failure"))
      oldDirectory.resolve({ cwd: "/stale-host" })
      await flushPromises()
      expect(w.text()).toContain("worker-only")
      expect(w.text()).not.toContain("stale")
      expect(pwdInput(w).element.value).toBe("/worker")
      w.unmount()
    },
  )

  it("asks for a machine and sends nothing when the machine goes away", async () => {
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    await w.find('[data-test="add-config-general"]').trigger("click")
    configAPI.listCreatures.mockClear()
    configAPI.getServerInfo.mockClear()
    await w.find('[data-testid="site"]').setValue("")
    await flushPromises()
    expect(configAPI.listCreatures).not.toHaveBeenCalled()
    expect(configAPI.getServerInfo).not.toHaveBeenCalled()
    expect(w.find('[role="alert"]').text()).toBe("Select a machine to run on.")
    expect(submit(w).element.disabled).toBe(true)
    w.unmount()
  })

  it("keeps a typed working directory when the machine changes", async () => {
    configAPI.getServerInfo
      .mockResolvedValueOnce({ cwd: "/host" })
      .mockResolvedValueOnce({ cwd: "/worker" })
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    await pwdInput(w).setValue("/my/project")
    await w.find('[data-testid="site"]').setValue("worker-1")
    await flushPromises()
    expect(configAPI.getServerInfo).toHaveBeenLastCalledWith({ onNode: "worker-1" })
    expect(pwdInput(w).element.value).toBe("/my/project")
    w.unmount()
  })
})

describe("NewSessionDialog: terrarium", () => {
  it("starts a recipe, and in a cluster only on a worker", async () => {
    cluster.isCluster = true
    const w = mount(NewSessionDialog, { props: { mode: "terrarium" }, attachTo: document.body })
    await flushPromises()
    expect(configAPI.listTerrariums).toHaveBeenCalled()
    await w.find('[data-test="add-config-swe_team"]').trigger("click")
    expect(submit(w).element.disabled).toBe(true)
    await w.find('[data-testid="site"]').setValue("_host")
    await flushPromises()
    await w.find('[data-test="add-config-swe_team"]').trigger("click")
    expect(submit(w).element.disabled).toBe(true)
    await w.find('[data-testid="site"]').setValue("worker-1")
    await flushPromises()
    await w.find('[data-test="add-config-swe_team"]').trigger("click")
    expect(submit(w).element.disabled).toBe(false)
    await submit(w).trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "terrarium",
        configPath: "@pkg/terrariums/swe_team",
        name: "random-terrarium",
        onNode: "worker-1",
      }),
    )
    w.unmount()
  })

  it("switching mode clears the choice and re-rolls an untouched name", async () => {
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await flushPromises()
    await w.find('[data-test="add-config-general"]').trigger("click")
    await w.find('[data-test="new-mode-terrarium"]').trigger("click")
    await flushPromises()
    expect(configInput(w).element.value).toBe("")
    expect(w.find('[data-test="new-name"]').attributes("placeholder")).toBe("random-terrarium")
    w.unmount()
  })
})

describe("NewSessionDialog: surroundings", () => {
  it("starts without opening a tab when silent, and shows a failure in place", async () => {
    createSession.mockRejectedValueOnce({ response: { data: { detail: "config is broken" } } })
    const w = mount(NewSessionDialog, { props: { silent: true }, attachTo: document.body })
    await flushPromises()
    expect(w.find('[data-test="new-open-chat"]').exists()).toBe(false)
    await w.find('[data-test="add-config-general"]').trigger("click")
    await submit(w).trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith(expect.objectContaining({ attachMode: "none" }))
    expect(w.find('[data-test="new-error"]').text()).toBe("config is broken")
    expect(w.emitted("close")).toBeUndefined()
    w.unmount()
  })

  it("sends resume to History, and closes on Esc", async () => {
    const w = mount(NewSessionDialog, { attachTo: document.body })
    await w.find('[data-test="new-history"]').trigger("click")
    expect(openTab).toHaveBeenCalledWith({ kind: "saved-sessions", id: "saved-sessions" })
    expect(w.emitted("close")).toHaveLength(1)
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    expect(w.emitted("close")).toHaveLength(2)
    w.unmount()
  })
})
