import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    updateUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
    getUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
  },
}))

import { settingsAPI } from "@/utils/api"
import { _resetUIPrefsForTests, ensureUIPrefsLoaded } from "@/utils/uiPrefs"
import { DEFAULT_READING_SIZE, READING_SIZES, useThemeStore } from "./theme"

const READING_SIZE_KEY = "kt-reading-size"

function createStorage() {
  const values = new Map()
  return {
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  }
}

describe("theme reading size", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    _resetUIPrefsForTests()
    vi.clearAllMocks()
    settingsAPI.getUIPrefs.mockResolvedValue({ values: {} })
    settingsAPI.updateUIPrefs.mockImplementation(async (values) => ({ values }))
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: createStorage(),
    })
    window.matchMedia = vi.fn().mockReturnValue({ matches: false })
    setActivePinia(createPinia())
    localStorage.clear()
    document.documentElement.removeAttribute("data-reading-size")
    document.documentElement.style.zoom = ""
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("defaults to the unchanged interface size and applies it independently from UI zoom", () => {
    const theme = useThemeStore()

    theme.init()
    theme.setDesktopZoom(1.25)

    expect(theme.readingSize).toBe(DEFAULT_READING_SIZE)
    expect(document.documentElement.dataset.readingSize).toBe(DEFAULT_READING_SIZE)
    expect(document.documentElement.style.zoom).toBe("1.25")
  })

  it.each(READING_SIZES)("persists and applies the %s reading size", (size) => {
    const theme = useThemeStore()

    theme.setReadingSize(size)

    expect(theme.readingSize).toBe(size)
    expect(document.documentElement.dataset.readingSize).toBe(size)
    expect(localStorage.getItem(READING_SIZE_KEY)).toBe(size)
  })

  it("falls back to the default for an unsupported stored reading size", () => {
    localStorage.setItem(READING_SIZE_KEY, "huge")

    const theme = useThemeStore()
    theme.init()

    expect(theme.readingSize).toBe(DEFAULT_READING_SIZE)
    expect(document.documentElement.dataset.readingSize).toBe(DEFAULT_READING_SIZE)
  })

  it("applies a backend reading size that arrives after the boot timeout", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )

    const boot = ensureUIPrefsLoaded({ timeoutMs: 2500 })
    await vi.advanceTimersByTimeAsync(2500)
    await boot

    const theme = useThemeStore()
    theme.init()
    resolvePrefs({ values: { [READING_SIZE_KEY]: "larger" } })
    await vi.runAllTimersAsync()

    expect(theme.readingSize).toBe("larger")
    expect(document.documentElement.dataset.readingSize).toBe("larger")
    expect(localStorage.getItem(READING_SIZE_KEY)).toBe("larger")
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalledWith(
      expect.objectContaining({ [READING_SIZE_KEY]: DEFAULT_READING_SIZE }),
    )
  })

  it("keeps a user reading-size change made while backend preferences load", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )

    const boot = ensureUIPrefsLoaded({ timeoutMs: 2500 })
    await vi.advanceTimersByTimeAsync(2500)
    await boot

    const theme = useThemeStore()
    theme.init()
    theme.setReadingSize("large")
    resolvePrefs({ values: { [READING_SIZE_KEY]: "larger" } })
    await vi.runAllTimersAsync()

    expect(theme.readingSize).toBe("large")
    expect(document.documentElement.dataset.readingSize).toBe("large")
    expect(localStorage.getItem(READING_SIZE_KEY)).toBe("large")
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith(
      expect.objectContaining({ [READING_SIZE_KEY]: "large" }),
    )
  })

  it("normalizes an unsupported backend reading size that arrives late", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )

    const boot = ensureUIPrefsLoaded({ timeoutMs: 2500 })
    await vi.advanceTimersByTimeAsync(2500)
    await boot

    const theme = useThemeStore()
    theme.init()
    resolvePrefs({ values: { [READING_SIZE_KEY]: "huge" } })
    await vi.runAllTimersAsync()

    expect(theme.readingSize).toBe(DEFAULT_READING_SIZE)
    expect(document.documentElement.dataset.readingSize).toBe(DEFAULT_READING_SIZE)
    expect(localStorage.getItem(READING_SIZE_KEY)).toBe(DEFAULT_READING_SIZE)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith(
      expect.objectContaining({ [READING_SIZE_KEY]: DEFAULT_READING_SIZE }),
    )
  })
})
