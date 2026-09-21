import { flushPromises, shallowMount } from "@vue/test-utils"
import { createPinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  sessionAPI: { list: vi.fn(), listActive: vi.fn() },
  settingsAPI: { getUIPrefs: vi.fn(), updateUIPrefs: vi.fn() },
  agentAPI: {},
  terrariumAPI: {},
  configAPI: {},
  statsAPI: {},
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import Dashboard from "./Dashboard.vue"
import { sessionAPI, settingsAPI } from "@/utils/api"
import { _resetUIPrefsForTests, ensureUIPrefsLoaded } from "@/utils/uiPrefs"

const KEY = "kt.dashboard.refreshIntervalMs"
let wrappers
let backendPrefs

async function openDashboard() {
  const wrapper = shallowMount(Dashboard, { global: { plugins: [createPinia()] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  _resetUIPrefsForTests()
  wrappers = []
  backendPrefs = {}
  const storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  sessionAPI.list.mockResolvedValue({ sessions: [] })
  sessionAPI.listActive.mockResolvedValue([])
  settingsAPI.getUIPrefs.mockImplementation(async () => ({ values: { ...backendPrefs } }))
  settingsAPI.updateUIPrefs.mockImplementation(async (values) => {
    Object.assign(backendPrefs, values)
    return { values: { ...backendPrefs } }
  })
})

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount())
  _resetUIPrefsForTests()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe("Dashboard refresh preference", () => {
  it("loads immediately and defaults to a five-second poll", async () => {
    const wrapper = await openDashboard()
    expect(wrapper.get("select").element.value).toBe("5000")
    expect(sessionAPI.list).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(4999)
    expect(sessionAPI.list).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(sessionAPI.list).toHaveBeenCalledTimes(2)
  })

  it.each([0, 5000, 15000, 60000])(
    "retains %i ms across remount and a fresh client",
    async (interval) => {
      let wrapper = await openDashboard()
      await wrapper.get("select").setValue("15000")
      await wrapper.get("select").setValue(String(interval))
      expect(localStorage.getItem(KEY)).toBe(String(interval))
      await vi.advanceTimersByTimeAsync(1500)
      expect(backendPrefs[KEY]).toBe(interval)

      wrapper.unmount()
      sessionAPI.list.mockClear()
      wrapper = await openDashboard()
      expect(wrapper.get("select").element.value).toBe(String(interval))
      expect(sessionAPI.list).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(interval || 60000)
      expect(sessionAPI.list).toHaveBeenCalledTimes(interval ? 2 : 1)

      wrapper.unmount()
      _resetUIPrefsForTests()
      localStorage.clear()
      await ensureUIPrefsLoaded()
      sessionAPI.list.mockClear()
      wrapper = await openDashboard()
      expect(wrapper.get("select").element.value).toBe(String(interval))
      expect(sessionAPI.list).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(interval || 60000)
      expect(sessionAPI.list).toHaveBeenCalledTimes(interval ? 2 : 1)
      wrapper.unmount()
      const count = sessionAPI.list.mock.calls.length
      await vi.advanceTimersByTimeAsync(60000)
      expect(sessionAPI.list).toHaveBeenCalledTimes(count)
    },
  )

  it("replaces the current timer and stops periodic refresh when Off is selected", async () => {
    const wrapper = await openDashboard()
    await vi.advanceTimersByTimeAsync(2000)
    await wrapper.get("select").setValue("15000")
    await vi.advanceTimersByTimeAsync(14999)
    expect(sessionAPI.list).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(sessionAPI.list).toHaveBeenCalledTimes(2)
    await wrapper.get("select").setValue("0")
    await vi.advanceTimersByTimeAsync(60000)
    expect(sessionAPI.list).toHaveBeenCalledTimes(2)
  })

  it.each(["garbage", "", "-1", "1000", "Infinity", "true"])(
    "uses the default for invalid stored value %j",
    async (value) => {
      localStorage.setItem(KEY, value)
      const wrapper = await openDashboard()
      expect(wrapper.get("select").element.value).toBe("5000")
      await vi.advanceTimersByTimeAsync(5000)
      expect(sessionAPI.list).toHaveBeenCalledTimes(2)
    },
  )

  it("restores a backend preference that arrives after the startup timeout", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )
    const loading = ensureUIPrefsLoaded({ timeoutMs: 2500 })
    await vi.advanceTimersByTimeAsync(2500)
    await loading
    const wrapper = await openDashboard()
    resolvePrefs({ values: { [KEY]: 0 } })
    await flushPromises()
    expect(wrapper.get("select").element.value).toBe("0")
    await vi.advanceTimersByTimeAsync(60000)
    expect(sessionAPI.list).toHaveBeenCalledTimes(1)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
  })

  it("keeps a newer user choice when a delayed backend preference arrives", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )
    const wrapper = await openDashboard()
    await wrapper.get("select").setValue("60000")
    resolvePrefs({ values: { [KEY]: 0 } })
    await flushPromises()
    expect(wrapper.get("select").element.value).toBe("60000")
    await vi.advanceTimersByTimeAsync(60000)
    expect(sessionAPI.list).toHaveBeenCalledTimes(2)
    expect(backendPrefs[KEY]).toBe(60000)
  })

  it("does not restart polling after unmount when preference loading completes", async () => {
    let resolvePrefs
    settingsAPI.getUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePrefs = resolve)),
    )
    const wrapper = await openDashboard()
    wrapper.unmount()
    resolvePrefs({ values: { [KEY]: 15000 } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60000)
    expect(sessionAPI.list).toHaveBeenCalledTimes(1)
  })
})
