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

vi.mock("@/utils/api", () => ({
  configAPI: {
    getServerInfo: vi.fn(),
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
  useTabsStore: () => ({ createSession: vi.fn() }),
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
