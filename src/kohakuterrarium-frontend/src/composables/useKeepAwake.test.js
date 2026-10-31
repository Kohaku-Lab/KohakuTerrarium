import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import {
  HOLD_OFF_MS,
  keepingAwake,
  shouldStayAwake,
  useKeepAwake,
} from "@/composables/useKeepAwake"
import { resetShellSettings } from "@/composables/useShellSettings"
import { clearAttentionRegistry, createAttentionState, publishAttention } from "@/stores/attention"

function shell(keepAwake = "running") {
  const calls = []
  const invoke = vi.fn(async (name, payload) => {
    if (name === "get_shell_settings") return { keepAwake }
    if (name === "set_keep_awake") {
      calls.push(payload.args[0])
      return true
    }
    return null
  })
  window.__TAURI_INTERNALS__ = { invoke }
  return calls
}

function mountAwake() {
  return mount(
    defineComponent({
      setup() {
        useKeepAwake()
        return () => null
      },
    }),
  )
}

const turn = (running) =>
  publishAttention("g", "root", { ...createAttentionState(), processing: running })

beforeEach(() => {
  vi.useFakeTimers()
  clearAttentionRegistry()
  resetShellSettings()
  keepingAwake.value = false
  Object.defineProperty(navigator, "getBattery", { value: undefined, configurable: true })
})

afterEach(() => {
  vi.useRealTimers()
  delete window.__TAURI_INTERNALS__
  clearAttentionRegistry()
  resetShellSettings()
})

describe("shouldStayAwake", () => {
  it("follows the setting, the running turns and the power source", () => {
    expect(shouldStayAwake("never", 3, true)).toBe(false)
    expect(shouldStayAwake("running", 0, true)).toBe(false)
    expect(shouldStayAwake("running", 1, false)).toBe(true)
    expect(shouldStayAwake("running-on-ac", 1, true)).toBe(true)
    expect(shouldStayAwake("running-on-ac", 1, false)).toBe(false)
    expect(shouldStayAwake("running-on-ac", 1, null)).toBe(true)
  })
})

describe("useKeepAwake", () => {
  it("holds the computer awake while a turn runs and lets go after the hold-off", async () => {
    const calls = shell()
    const wrapper = mountAwake()
    await flushPromises()
    expect(calls).toEqual([])

    turn(true)
    await flushPromises()
    expect(calls).toEqual([true])
    expect(keepingAwake.value).toBe(true)

    turn(false)
    await vi.advanceTimersByTimeAsync(HOLD_OFF_MS - 1000)
    expect(calls).toEqual([true])
    turn(true)
    await vi.advanceTimersByTimeAsync(HOLD_OFF_MS * 2)
    expect(calls).toEqual([true])

    turn(false)
    await vi.advanceTimersByTimeAsync(HOLD_OFF_MS + 100)
    expect(calls).toEqual([true, false])
    expect(keepingAwake.value).toBe(false)
    wrapper.unmount()
  })

  it("never holds when the setting says never", async () => {
    const calls = shell("never")
    const wrapper = mountAwake()
    await flushPromises()
    turn(true)
    await flushPromises()
    expect(calls).toEqual([])
    wrapper.unmount()
  })

  it("does not hold on battery when the setting wants mains power", async () => {
    const calls = shell("running-on-ac")
    const battery = new EventTarget()
    battery.charging = false
    Object.defineProperty(navigator, "getBattery", {
      value: async () => battery,
      configurable: true,
    })
    const wrapper = mountAwake()
    await flushPromises()
    turn(true)
    await flushPromises()
    expect(calls).toEqual([])

    battery.charging = true
    battery.dispatchEvent(new Event("chargingchange"))
    await flushPromises()
    expect(calls).toEqual([true])
    wrapper.unmount()
  })

  it("lets go when the page goes away and does nothing outside the Tauri shell", async () => {
    const calls = shell()
    const wrapper = mountAwake()
    await flushPromises()
    turn(true)
    await flushPromises()
    wrapper.unmount()
    await flushPromises()
    expect(calls).toEqual([true, false])

    delete window.__TAURI_INTERNALS__
    const browser = mountAwake()
    await flushPromises()
    turn(true)
    await flushPromises()
    expect(keepingAwake.value).toBe(false)
    browser.unmount()
  })
})
