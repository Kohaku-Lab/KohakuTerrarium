import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    updateUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
    getUIPrefs: vi.fn().mockResolvedValue({ values: {} }),
  },
}))

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
})
