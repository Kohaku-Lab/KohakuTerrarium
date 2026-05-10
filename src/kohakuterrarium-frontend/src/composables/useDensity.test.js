import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  DENSITIES,
  _resetDensityForTests,
  densityRank,
  meetsDensity,
  useDensity,
} from "./useDensity.js"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"

let storage

function setViewportWidth(w) {
  // jsdom keeps innerWidth writable; mutate then dispatch resize.
  window.innerWidth = w
  window.dispatchEvent(new Event("resize"))
}

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  _resetUIPrefsForTests()
  setViewportWidth(1400)
  _resetDensityForTests()
})

afterEach(() => {
  vi.unstubAllGlobals()
  _resetUIPrefsForTests()
  setViewportWidth(1400)
  _resetDensityForTests()
})

describe("useDensity", () => {
  it("derives compact below 768px", () => {
    setViewportWidth(500)
    const { density, isCompact } = useDensity()
    expect(density.value).toBe("compact")
    expect(isCompact.value).toBe(true)
  })

  it("derives regular between 768px and 1280px", () => {
    setViewportWidth(1024)
    const { density, isRegular } = useDensity()
    expect(density.value).toBe("regular")
    expect(isRegular.value).toBe(true)
  })

  it("derives expansive at or above 1280px", () => {
    setViewportWidth(1600)
    const { density, isExpansive } = useDensity()
    expect(density.value).toBe("expansive")
    expect(isExpansive.value).toBe(true)
  })

  it("override pins density regardless of viewport", () => {
    setViewportWidth(1600)
    const { density, setOverride } = useDensity()
    setOverride("compact")
    expect(density.value).toBe("compact")
  })

  it("override 'auto' falls back to viewport-derived density", () => {
    setViewportWidth(500)
    const { density, setOverride } = useDensity()
    setOverride("regular")
    expect(density.value).toBe("regular")
    setOverride("auto")
    expect(density.value).toBe("compact")
  })

  it("ignores invalid override values", () => {
    setViewportWidth(500)
    const { density, setOverride } = useDensity()
    setOverride("nonsense")
    expect(density.value).toBe("compact")
  })

  it("updates reactively on resize", () => {
    setViewportWidth(1400)
    const { density } = useDensity()
    expect(density.value).toBe("expansive")
    setViewportWidth(700)
    expect(density.value).toBe("compact")
  })
})

describe("density rank helpers", () => {
  it("ranks densities compact < regular < expansive", () => {
    expect(densityRank("compact")).toBeLessThan(densityRank("regular"))
    expect(densityRank("regular")).toBeLessThan(densityRank("expansive"))
  })

  it("meetsDensity is satisfied at or above the required level", () => {
    expect(meetsDensity("compact", "compact")).toBe(true)
    expect(meetsDensity("compact", "regular")).toBe(false)
    expect(meetsDensity("regular", "regular")).toBe(true)
    expect(meetsDensity("expansive", "regular")).toBe(true)
  })

  it("DENSITIES exposes the canonical ordered list", () => {
    expect(DENSITIES).toEqual(["compact", "regular", "expansive"])
  })
})
