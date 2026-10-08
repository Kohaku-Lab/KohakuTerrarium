import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getUIPrefs: vi.fn(async () => ({ values: {} })),
    updateUIPrefs: vi.fn(async (values) => ({ values })),
  },
}))

import { settingsAPI } from "@/utils/api"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"

import { _resetUiVersionForTests, UI_VERSION_KEY, useUiVersion } from "./useUiVersion"

let storage
let resolvePrefs

beforeEach(() => {
  _resetUIPrefsForTests()
  _resetUiVersionForTests()
  vi.clearAllMocks()
  settingsAPI.getUIPrefs.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolvePrefs = resolve
      }),
  )
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("useUiVersion", () => {
  it("defaults to v2 and ignores the retired kt-ui-version key", () => {
    storage.set("kt-ui-version", "v1")
    expect(useUiVersion().uiVersion.value).toBe("v2")
  })

  it("reads a stored pick and rejects unknown values", () => {
    storage.set(UI_VERSION_KEY, "v1")
    expect(useUiVersion().isV2.value).toBe(false)
    _resetUiVersionForTests()
    storage.set(UI_VERSION_KEY, "v9")
    expect(useUiVersion().uiVersion.value).toBe("v2")
  })

  it("adopts the backend pref when it arrives after first use", async () => {
    const { uiVersion } = useUiVersion()
    expect(uiVersion.value).toBe("v2")
    resolvePrefs({ values: { [UI_VERSION_KEY]: "v1" } })
    await flush()
    expect(uiVersion.value).toBe("v1")
  })

  it("keeps the user's pick over a late backend value", async () => {
    const { uiVersion, setUiVersion } = useUiVersion()
    setUiVersion("v2")
    storage.delete(UI_VERSION_KEY)
    resolvePrefs({ values: { [UI_VERSION_KEY]: "v1" } })
    await flush()
    expect(uiVersion.value).toBe("v2")
  })

  it("toggles both ways and persists the pick under the new key", () => {
    const { uiVersion, setUiVersion } = useUiVersion()
    setUiVersion("v1")
    expect(uiVersion.value).toBe("v1")
    expect(storage.get(UI_VERSION_KEY)).toBe("v1")
    setUiVersion("v2")
    expect(uiVersion.value).toBe("v2")
    expect(storage.get(UI_VERSION_KEY)).toBe("v2")
    expect(storage.has("kt-ui-version")).toBe(false)
  })
})
