/**
 * B6 follow-up — NewCreatureModal / NewTerrariumModal must re-fetch the
 * server-info default working directory whenever the "Run on" site
 * (SitePicker `onNode`) changes, and must pass the selected node to the
 * API so the backend returns the worker-side workspace default (B5).
 *
 * Regression we're pinning:
 *   - Previously the modal only called `configAPI.getServerInfo()` once
 *     on mount, with no `on_node` argument, and there was no watcher on
 *     `onNode`. So if the user picked a remote site AFTER mount (which
 *     they will, now that SitePicker comes first per the B6 ordering
 *     fix), `pwd` would stay at the host's cwd — exactly the bug the
 *     B5 backend route was added to prevent.
 *
 * The two assertions per modal:
 *   1. The initial mount call passes `{ onNode: "_host" }` (the default).
 *   2. Changing `onNode` re-invokes `getServerInfo` with the new node
 *      AND the working-dir input is updated to the returned `cwd` —
 *      provided the user hasn't typed into the field yet.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { mount, flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const createSession = vi.hoisted(() => vi.fn())

vi.mock("@/utils/api", () => ({
  configAPI: {
    getServerInfo: vi.fn(),
    listCreatures: vi.fn(),
  },
}))

vi.mock("@/stores/configs", () => ({
  useConfigsStore: () => ({
    creatures: [],
    terrariums: [],
    fetchAll: vi.fn(),
  }),
}))

vi.mock("@/stores/tabs", () => ({
  useTabsStore: () => ({ createSession }),
}))

// SitePicker is replaced by a tiny stub that exposes an input we can
// drive — its real implementation hides itself in standalone mode and
// would never emit `update:modelValue` here.
vi.mock("@/components/cluster/SitePicker.vue", () => ({
  default: {
    name: "SitePicker",
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template: `<select data-testid="site-picker" :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
      <option value="">Select a site</option>
      <option value="_host">host</option>
      <option value="worker-1">worker-1</option>
    </select>`,
  },
}))

vi.mock("@/components/common/ModalShell.vue", () => ({
  default: {
    name: "ModalShell",
    template: `<div><slot name="title" /><slot /><slot name="footer" /></div>`,
  },
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k) => k }),
}))

vi.mock("@/utils/randomName", () => ({
  randomNameFor: () => "test-name",
}))

import { configAPI } from "@/utils/api"
import NewCreatureModal from "./NewCreatureModal.vue"
import NewTerrariumModal from "./NewTerrariumModal.vue"

beforeEach(() => {
  setActivePinia(createPinia())
  configAPI.getServerInfo.mockReset()
  configAPI.listCreatures.mockReset().mockResolvedValue([])
})

describe("NewCreatureModal — execution-node catalog", () => {
  const choice = (name) => ({ name, path: `@${name}/creatures/general` })
  const deferred = () => {
    let resolve, reject
    const promise = new Promise((yes, no) => {
      resolve = yes
      reject = no
    })
    return { promise, resolve, reject }
  }

  it("invalidates discovery and submission when the execution site disappears", async () => {
    configAPI.getServerInfo.mockResolvedValue({ cwd: "/work" })
    configAPI.listCreatures.mockResolvedValue([choice("worker-only")])
    const wrapper = mount(NewCreatureModal)
    await flushPromises()
    await wrapper.find('[data-testid="site-picker"]').setValue("worker-1")
    await flushPromises()
    await wrapper.find('input[type="radio"]').setValue(true)
    configAPI.listCreatures.mockClear()
    configAPI.getServerInfo.mockClear()
    await wrapper.find('[data-testid="site-picker"]').setValue("")
    await flushPromises()
    expect(configAPI.listCreatures).not.toHaveBeenCalled()
    expect(configAPI.getServerInfo).not.toHaveBeenCalled()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    expect(wrapper.find('[role="alert"]').text()).toBe("Select an execution site.")
    expect(wrapper.findAll("button").at(-1).element.disabled).toBe(true)
    wrapper.unmount()
  })

  it("loads the selected node and clears the previous choice", async () => {
    configAPI.getServerInfo.mockResolvedValue({ cwd: "/work" })
    configAPI.listCreatures
      .mockResolvedValueOnce([choice("host-only")])
      .mockResolvedValueOnce([choice("worker-only")])
    const wrapper = mount(NewCreatureModal)
    await flushPromises()
    expect(configAPI.listCreatures).toHaveBeenCalledWith({ onNode: "_host" })
    await wrapper.find('input[type="radio"]').setValue(true)
    expect(wrapper.findAll("button").at(-1).element.disabled).toBe(false)
    await wrapper.find('[data-testid="site-picker"]').setValue("worker-1")
    expect(wrapper.findAll("button").at(-1).element.disabled).toBe(true)
    await flushPromises()
    expect(configAPI.listCreatures).toHaveBeenLastCalledWith({ onNode: "worker-1" })
    expect(wrapper.text()).toContain("worker-only")
    expect(wrapper.text()).not.toContain("host-only")
    expect(wrapper.find('input[type="radio"]').element.checked).toBe(false)
    await wrapper.find('input[type="radio"]').setValue(true)
    await wrapper.findAll("button").at(-1).trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "creature",
        configPath: "@worker-only/creatures/general",
        onNode: "worker-1",
      }),
    )
    wrapper.unmount()
  })

  it("shows a discovery failure without retaining another node's choices", async () => {
    configAPI.getServerInfo.mockResolvedValue({ cwd: "/work" })
    configAPI.listCreatures
      .mockResolvedValueOnce([choice("host-only")])
      .mockRejectedValueOnce(new Error("Worker catalog unavailable"))
    const wrapper = mount(NewCreatureModal)
    await flushPromises()
    await wrapper.find('[data-testid="site-picker"]').setValue("worker-1")
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toBe("Worker catalog unavailable")
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    expect(wrapper.findAll("button").at(-1).element.disabled).toBe(true)
    wrapper.unmount()
  })

  it.each(["success", "failure"])(
    "ignores an older catalog %s and working directory",
    async (outcome) => {
      const oldCatalog = deferred()
      const oldDirectory = deferred()
      configAPI.listCreatures
        .mockReturnValueOnce(oldCatalog.promise)
        .mockResolvedValueOnce([choice("worker-only")])
      configAPI.getServerInfo
        .mockReturnValueOnce(oldDirectory.promise)
        .mockResolvedValueOnce({ cwd: "/worker" })
      const wrapper = mount(NewCreatureModal)
      await wrapper.find('[data-testid="site-picker"]').setValue("worker-1")
      await flushPromises()
      if (outcome === "success") oldCatalog.resolve([choice("stale-host")])
      else oldCatalog.reject(new Error("stale failure"))
      oldDirectory.resolve({ cwd: "/stale-host" })
      await flushPromises()
      expect(wrapper.text()).toContain("worker-only")
      expect(wrapper.text()).not.toContain("stale")
      expect(wrapper.find('input[placeholder="/home/user/my-project"]').element.value).toBe(
        "/worker",
      )
      wrapper.unmount()
    },
  )
})

afterEach(() => {
  vi.clearAllMocks()
})

describe.each([
  ["NewCreatureModal", NewCreatureModal],
  ["NewTerrariumModal", NewTerrariumModal],
])("%s — B6 follow-up: per-node working-dir refresh", (name, Component) => {
  it("passes onNode to getServerInfo on mount and re-fetches when the site changes", async () => {
    // Initial mount returns the host default; the site-change returns a
    // worker-side path. Both shapes match the backend's contract.
    configAPI.getServerInfo
      .mockResolvedValueOnce({ cwd: "/host/cwd", platform: "linux" })
      .mockResolvedValueOnce({ cwd: "/home/worker", platform: "linux" })

    const wrapper = mount(Component)
    await flushPromises()

    // (1) Mount fetched with the default _host node.
    expect(configAPI.getServerInfo).toHaveBeenCalledTimes(1)
    expect(configAPI.getServerInfo).toHaveBeenNthCalledWith(1, { onNode: "_host" })
    expect(wrapper.find('input[placeholder="/home/user/my-project"]').element.value).toBe(
      "/host/cwd",
    )

    // (2) User picks a different site → modal must re-fetch with the new
    // node AND update the working-dir input to the worker-side default.
    const picker = wrapper.find('[data-testid="site-picker"]')
    await picker.setValue("worker-1")
    await flushPromises()

    expect(configAPI.getServerInfo).toHaveBeenCalledTimes(2)
    expect(configAPI.getServerInfo).toHaveBeenNthCalledWith(2, { onNode: "worker-1" })
    expect(wrapper.find('input[placeholder="/home/user/my-project"]').element.value).toBe(
      "/home/worker",
    )
  })

  it("does not overwrite the working-dir input if the user has typed a path", async () => {
    configAPI.getServerInfo
      .mockResolvedValueOnce({ cwd: "/host/cwd", platform: "linux" })
      .mockResolvedValueOnce({ cwd: "/home/worker", platform: "linux" })

    const wrapper = mount(Component)
    await flushPromises()

    const pwdInput = wrapper.find('input[placeholder="/home/user/my-project"]')
    // Simulate the user typing — this should set the user-touched flag.
    await pwdInput.setValue("/my/custom/path")
    await pwdInput.trigger("input")

    const picker = wrapper.find('[data-testid="site-picker"]')
    await picker.setValue("worker-1")
    await flushPromises()

    // Re-fetch still happens (the modal can't know whether to skip
    // until it sees the user-touched flag), but the input value MUST
    // stay at the user's path.
    expect(configAPI.getServerInfo).toHaveBeenNthCalledWith(2, { onNode: "worker-1" })
    expect(pwdInput.element.value).toBe("/my/custom/path")
  })
})
