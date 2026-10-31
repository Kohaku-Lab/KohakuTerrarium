import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import { formatMemory, formatUptime, useBackendStatus } from "@/composables/useBackendStatus"
import DesktopBackendBanner from "@/components/chrome/DesktopBackendBanner.vue"

vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

afterEach(() => {
  delete window.__TAURI_INTERNALS__
})

function mountStatus() {
  let api
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useBackendStatus()
        return () => null
      },
    }),
  )
  return { wrapper, api }
}

describe("formatting", () => {
  it("describes durations by their two largest units", () => {
    expect(formatUptime(12)).toBe("12s")
    expect(formatUptime(423)).toBe("7m 3s")
    expect(formatUptime(7500)).toBe("2h 5m")
    expect(formatUptime(3 * 86400 + 4 * 3600 + 59)).toBe("3d 4h")
    expect(formatUptime(-5)).toBe("0s")
    expect(formatUptime(1.9)).toBe("1s")
  })

  it("rounds memory to whole megabytes", () => {
    expect(formatMemory(612 * 1024 * 1024 + 1000)).toBe("612 MB")
    expect(formatMemory(0)).toBe("0 MB")
  })
})

describe("useBackendStatus", () => {
  it("stays empty in a browser", async () => {
    const { wrapper, api } = mountStatus()
    await flushPromises()
    expect(api.status.value).toBeNull()
    expect(await api.restart()).toBeUndefined()
    expect(api.status.value).toBeNull()
    wrapper.unmount()
  })

  it("reads the shell's status and marks a restart as it starts", async () => {
    const invoke = vi.fn(async (name) =>
      name === "get_backend_status"
        ? { state: "running", restarts: 0, memoryBytes: 1 }
        : name === "restart_backend",
    )
    window.__TAURI_INTERNALS__ = { invoke }
    const { wrapper, api } = mountStatus()
    await flushPromises()
    expect(api.status.value.state).toBe("running")

    await api.restart()
    expect(invoke).toHaveBeenCalledWith("restart_backend", {})
    expect(api.status.value).toMatchObject({ state: "restarting", memoryBytes: 1 })
    wrapper.unmount()
  })

  it("does not claim a restart the shell refused", async () => {
    const invoke = vi.fn(async (name) =>
      name === "get_backend_status" ? { state: "restarting" } : false,
    )
    window.__TAURI_INTERNALS__ = { invoke }
    const { wrapper, api } = mountStatus()
    await flushPromises()
    api.status.value = { state: "running" }
    await api.restart()
    expect(api.status.value.state).toBe("running")
    wrapper.unmount()
  })
})

describe("DesktopBackendBanner", () => {
  const emit = (state, message = "") =>
    window.dispatchEvent(
      new CustomEvent("kt:desktop-backend-state", { detail: { state, message } }),
    )

  it("shows while the backend restarts or has stopped, and hides when it runs", async () => {
    const wrapper = mount(DesktopBackendBanner)
    expect(wrapper.find('[data-test="backend-banner"]').exists()).toBe(false)

    emit("restarting")
    await flushPromises()
    expect(wrapper.get('[data-test="backend-banner"]').text()).toContain(
      "desktop.backend.restarting",
    )
    expect(wrapper.find('[data-banner="restart"]').exists()).toBe(false)

    emit("failed", "port in use")
    await flushPromises()
    expect(wrapper.get('[data-test="backend-banner"]').text()).toContain("port in use")
    expect(wrapper.find('[data-banner="restart"]').exists()).toBe(true)

    emit("running")
    await flushPromises()
    expect(wrapper.find('[data-test="backend-banner"]').exists()).toBe(false)
    wrapper.unmount()
    emit("failed")
    await flushPromises()
    expect(wrapper.find('[data-test="backend-banner"]').exists()).toBe(false)
  })

  it("restarts the backend and opens the log from the stopped banner", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mount(DesktopBackendBanner)
    emit("failed")
    await flushPromises()

    await wrapper.get('[data-banner="restart"]').trigger("click")
    expect(invoke).toHaveBeenLastCalledWith("restart_backend", {})
    await wrapper.get('[data-banner="logs"]').trigger("click")
    expect(invoke).toHaveBeenLastCalledWith("open_logs", {})
    expect(wrapper.get('[data-test="backend-banner"]').text()).toContain("desktop.backend.failed")
    wrapper.unmount()
  })
})
