import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    updateUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
    getUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
  },
}))

import { settingsAPI } from "@/utils/api"
import { _resetUIPrefsForTests, ensureUIPrefsLoaded } from "@/utils/uiPrefs"
import { initializeAttentionPrefs, useAttentionPrefs } from "./attentionPrefs"

function createStorage() {
  const values = new Map()
  return {
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  }
}

describe("attention preferences", () => {
  beforeEach(() => {
    _resetUIPrefsForTests()
    vi.clearAllMocks()
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: createStorage(),
    })
  })

  it("hydrates backend preferences after the boot load settles", async () => {
    settingsAPI.getUIPrefs.mockResolvedValue({
      values: {
        "kt.attention.desktopAttention": false,
        "kt.attention.systemNotifications": true,
      },
    })

    initializeAttentionPrefs()
    await ensureUIPrefsLoaded()
    await Promise.resolve()

    const { state } = useAttentionPrefs()
    expect(state.desktopAttention).toBe(false)
    expect(state.systemNotifications).toBe(true)
  })
})
