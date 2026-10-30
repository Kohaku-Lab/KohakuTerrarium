import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { shimsApi } = vi.hoisted(() => ({
  shimsApi: { status: vi.fn(), install: vi.fn(), uninstall: vi.fn() },
}))

vi.mock("@/utils/appShimsApi", () => ({ appShimsAPI: shimsApi }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params = {}) =>
      Object.keys(params).length ? `${key}:${JSON.stringify(params)}` : key,
  }),
}))

import DesktopAppSettings from "./DesktopAppSettings.vue"

const COMMANDS = ["kt", "kt-cli", "kt-tui"]

function status(overrides = {}) {
  return {
    local: true,
    supported: true,
    commands: COMMANDS,
    installed: [],
    target_dir: "C:/kt/bin",
    on_path: false,
    ...overrides,
  }
}

const ElSwitch = {
  props: ["modelValue", "loading"],
  emits: ["change"],
  template:
    '<button class="switch" :data-on="modelValue" @click="$emit(\'change\', !modelValue)" />',
}

const ElButton = {
  props: ["loading"],
  template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
}

function mountSettings() {
  return mount(DesktopAppSettings, {
    global: {
      stubs: { ElSwitch, "el-switch": ElSwitch, ElButton, "el-button": ElButton },
    },
  })
}

beforeEach(() => {
  Object.values(shimsApi).forEach((fn) => fn.mockReset())
  delete window.pywebview
})

afterEach(() => {
  delete window.pywebview
})

describe("DesktopAppSettings", () => {
  it("renders nothing for a server that cannot manage terminal commands", async () => {
    shimsApi.status.mockRejectedValue(new Error("404"))
    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.find("[data-desktop-app-settings]").exists()).toBe(false)
  })

  it("hides the terminal command for a browser on another machine", async () => {
    shimsApi.status.mockResolvedValue(status({ local: false }))
    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.find("[data-setting='terminal-command']").exists()).toBe(false)
  })

  it("installs the commands when switched on and tells the user to open a new terminal", async () => {
    shimsApi.status.mockResolvedValue(status())
    shimsApi.install.mockResolvedValue(status({ installed: COMMANDS, on_path: true }))
    const wrapper = mountSettings()
    await flushPromises()

    const toggle = wrapper.find("[data-setting='terminal-command'] .switch")
    expect(toggle.attributes("data-on")).toBe("false")
    await toggle.trigger("click")
    await flushPromises()

    expect(shimsApi.install).toHaveBeenCalledWith({ addToPath: true })
    expect(wrapper.find("[data-setting='terminal-command'] .switch").attributes("data-on")).toBe(
      "true",
    )
    expect(wrapper.find("[data-terminal-reopen]").exists()).toBe(true)
    expect(wrapper.find("[data-terminal-location]").text()).toContain("C:/kt/bin")
  })

  it("removes the commands and the PATH entry when switched off", async () => {
    shimsApi.status.mockResolvedValue(status({ installed: COMMANDS, on_path: true }))
    shimsApi.uninstall.mockResolvedValue(status())
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.find("[data-setting='terminal-command'] .switch").trigger("click")
    await flushPromises()

    expect(shimsApi.uninstall).toHaveBeenCalledWith({ removeFromPath: true })
    expect(wrapper.find("[data-setting='terminal-command'] .switch").attributes("data-on")).toBe(
      "false",
    )
  })

  it("shows the server's reason when installing fails and leaves the switch off", async () => {
    shimsApi.status.mockResolvedValue(status())
    shimsApi.install.mockRejectedValue({ response: { data: { detail: "read-only home" } } })
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.find("[data-setting='terminal-command'] .switch").trigger("click")
    await flushPromises()

    expect(wrapper.find("[data-terminal-error]").text()).toBe("read-only home")
    expect(wrapper.find("[data-setting='terminal-command'] .switch").attributes("data-on")).toBe(
      "false",
    )
  })

  it("offers start-at-login only when the shell supports it, and applies the shell's answer", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const api = {
      get_autostart: vi.fn(async () => false),
      set_autostart: vi.fn(async () => true),
    }
    window.pywebview = { api }
    const wrapper = mountSettings()
    await flushPromises()

    const toggle = wrapper.find("[data-setting='autostart'] .switch")
    expect(toggle.attributes("data-on")).toBe("false")
    await toggle.trigger("click")
    await flushPromises()

    expect(api.set_autostart).toHaveBeenCalledWith(true)
    expect(wrapper.find("[data-setting='autostart'] .switch").attributes("data-on")).toBe("true")
  })

  it("checks for a shell update, offers it, and installs it", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const api = {
      check_shell_update: vi.fn(async () => ({ available: true, version: "9.1.0" })),
      install_shell_update: vi.fn(async () => true),
    }
    window.pywebview = { api }
    const wrapper = mountSettings()
    await flushPromises()

    expect(wrapper.find("[data-update-install]").exists()).toBe(false)
    await wrapper.find("[data-update-check]").trigger("click")
    await flushPromises()

    expect(wrapper.find("[data-update-status]").text()).toContain("9.1.0")
    await wrapper.find("[data-update-install]").trigger("click")
    await flushPromises()
    expect(api.install_shell_update).toHaveBeenCalled()
  })

  it("says the app is current, and reports a failed check or install", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const api = {
      check_shell_update: vi.fn(async () => ({ available: false })),
      install_shell_update: vi.fn(async () => false),
    }
    window.pywebview = { api }
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.find("[data-update-check]").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-update-status]").text()).toContain("settings.desktop.updateCurrent")

    api.check_shell_update.mockResolvedValue({ available: false, error: "offline" })
    await wrapper.find("[data-update-check]").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-update-status]").text()).toContain("settings.desktop.updateFailed")
  })

  it("does not offer start-at-login in a plain browser", async () => {
    shimsApi.status.mockResolvedValue(status())
    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.find("[data-setting='autostart']").exists()).toBe(false)
  })
})
