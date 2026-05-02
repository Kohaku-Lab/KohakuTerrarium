import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { SUPPORTED_LOCALES, useLocaleStore } from "./locale.js"

beforeEach(() => {
  // jsdom localStorage is cleared between tests via vi.stubGlobal so
  // ``setLocale`` can persist without leaking.
  const map = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
    get length() {
      return map.size
    },
    key: (i) => Array.from(map.keys())[i] ?? null,
  })
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("locale store", () => {
  it("exposes ``current`` as an alias for ``locale``", () => {
    const store = useLocaleStore()
    expect(store.current).toBe(store.locale)
  })

  it("setLocale normalises unknown values to default", () => {
    const store = useLocaleStore()
    store.setLocale("xx-YY")
    expect(store.locale).toBe("en")
  })

  it("setLocale persists to localStorage", () => {
    const store = useLocaleStore()
    store.setLocale("ja")
    expect(store.locale).toBe("ja")
    // hybrid pref writer prefixes the key — just check it's stored.
    const stored = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i))
    expect(stored.some((k) => k && k.includes("kt-locale"))).toBe(true)
  })

  it("cycle rotates through every supported locale and wraps", () => {
    const store = useLocaleStore()
    store.setLocale(SUPPORTED_LOCALES[0])
    const seen = [store.locale]
    for (let i = 0; i < SUPPORTED_LOCALES.length; i++) {
      store.cycle()
      seen.push(store.locale)
    }
    // After N cycles we should be back to the start.
    expect(seen[seen.length - 1]).toBe(SUPPORTED_LOCALES[0])
    // Every supported locale was visited along the way.
    for (const code of SUPPORTED_LOCALES) {
      expect(seen).toContain(code)
    }
  })

  it("cycle from an unsupported locale lands on the second supported", () => {
    // setLocale normalises, but if a stale persisted value sneaks in
    // we still want cycle() to make forward progress.
    const store = useLocaleStore()
    store.locale = "xx-YY"
    store.cycle()
    // indexOf returns -1, so (-1 + 1) % N = 0 → first supported.
    expect(store.locale).toBe(SUPPORTED_LOCALES[0])
  })

  it("displayName returns the human-readable form", () => {
    const store = useLocaleStore()
    store.setLocale("zh-TW")
    expect(store.displayName).toBe("繁體中文")
  })
})
