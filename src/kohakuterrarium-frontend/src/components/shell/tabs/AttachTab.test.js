import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const INSTANCE = { id: "sid", graph_id: "sid", creatures: [{ name: "alice" }] }

vi.mock("@/stores/instances", () => ({
  useInstancesStore: () => ({ current: null, list: [], fetchOne: vi.fn(async () => INSTANCE) }),
}))
vi.mock("@/stores/chat", () => ({
  useChatStore: () => ({
    _instanceId: null,
    activeTab: null,
    attentionByTab: {},
    initForInstance: vi.fn(),
    resetForRouteSwitch: vi.fn(),
    markAttentionRead: vi.fn(),
  }),
}))
vi.mock("@/stores/editor", () => ({ useEditorStore: () => ({ openFile: vi.fn() }) }))
vi.mock("@/stores/layout", () => ({
  useLayoutStore: () => ({
    allPresets: {},
    activePresetId: null,
    loadInstanceOverrides: vi.fn(),
    getInstancePresetId: () => null,
    switchPreset: vi.fn(),
    rememberInstancePreset: vi.fn(),
  }),
}))
vi.mock("@/stores/tabs", () => ({
  useTabsStore: () => ({ tabGroups: {}, focusedGroup: null, detach: vi.fn() }),
}))
vi.mock("@/composables/useArtifactDetector", () => ({ useArtifactDetector: vi.fn() }))
vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: () => ({ start: vi.fn(), stop: vi.fn() }),
}))
vi.mock("@/components/session-v2/SessionShellV2.vue", () => ({
  __esModule: true,
  default: { template: "<div data-test='shell-v2' />" },
}))
vi.mock("@/components/shell/CompactWorkspaceShell.vue", () => ({
  default: { template: "<div data-test='shell-phone' />" },
}))
vi.mock("@/components/layout/WorkspaceShell.vue", () => ({
  default: { template: "<div data-test='shell-v1' />" },
}))

import AttachTab from "./AttachTab.vue"
import { _resetUiVersionForTests, useUiVersion } from "@/components/session-v2/model/useUiVersion"
import { _resetDensityForTests, useDensity } from "@/composables/useDensity"

async function shellShown() {
  const w = mount(AttachTab, { props: { tab: { id: "attach:sid", target: "sid" } } })
  await flushPromises()
  await vi.dynamicImportSettled()
  await flushPromises()
  const shown = ["shell-phone", "shell-v2", "shell-v1"].filter((t) =>
    w.find(`[data-test='${t}']`).exists(),
  )
  w.unmount()
  return shown
}

beforeEach(() => {
  setActivePinia(createPinia())
  _resetUiVersionForTests()
  _resetDensityForTests()
})

afterEach(() => {
  useDensity().setOverride("auto")
})

describe("AttachTab shell choice", () => {
  it("on a phone shows v2 (with its own phone layout), or the classic phone shell under v1", async () => {
    useDensity().setOverride("compact")
    useUiVersion().setUiVersion("v2")
    expect(await shellShown()).toEqual(["shell-v2"])
    useUiVersion().setUiVersion("v1")
    expect(await shellShown()).toEqual(["shell-phone"])
  })

  it("follows the desktop shell setting above phone width", async () => {
    useDensity().setOverride("regular")
    useUiVersion().setUiVersion("v2")
    expect(await shellShown()).toEqual(["shell-v2"])
    useUiVersion().setUiVersion("v1")
    expect(await shellShown()).toEqual(["shell-v1"])
  })
})
