import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { shimsApi, secretsApi, runtimeApi } = vi.hoisted(() => ({
  shimsApi: { status: vi.fn(), install: vi.fn(), uninstall: vi.fn() },
  secretsApi: { status: vi.fn(), setMode: vi.fn() },
  runtimeApi: { getRuntime: vi.fn() },
}))

vi.mock("@/utils/appShimsApi", () => ({ appShimsAPI: shimsApi }))
vi.mock("@/utils/appSecretsApi", () => ({ appSecretsAPI: secretsApi }))
vi.mock("@/utils/appUpdateApi", () => ({ appUpdateAPI: { getRuntime: runtimeApi.getRuntime } }))
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
  emits: ["click"],
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
  Object.values(secretsApi).forEach((fn) => fn.mockReset())
  runtimeApi.getRuntime.mockReset()
  runtimeApi.getRuntime.mockRejectedValue(new Error("404"))
  secretsApi.status.mockRejectedValue(new Error("404"))
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

  it("offers the system keychain only where one is reachable, and moves the keys both ways", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    secretsApi.status.mockResolvedValue({
      local: true,
      mode: "file",
      active: false,
      available: true,
      backend: "WinVaultKeyring",
    })
    secretsApi.setMode.mockResolvedValue({
      local: true,
      mode: "keychain",
      available: true,
      backend: "WinVaultKeyring",
    })
    const wrapper = mountSettings()
    await flushPromises()

    const toggle = () => wrapper.find("[data-setting='keychain'] .switch")
    expect(toggle().attributes("data-on")).toBe("false")
    expect(wrapper.text()).toContain("WinVaultKeyring")
    await toggle().trigger("click")
    await flushPromises()
    expect(secretsApi.setMode).toHaveBeenCalledWith("keychain")
    expect(toggle().attributes("data-on")).toBe("true")

    secretsApi.setMode.mockResolvedValue({
      local: true,
      mode: "file",
      available: true,
      backend: "WinVaultKeyring",
    })
    await toggle().trigger("click")
    await flushPromises()
    expect(secretsApi.setMode).toHaveBeenLastCalledWith("file")
    expect(toggle().attributes("data-on")).toBe("false")
  })

  it("keeps the switch where it was and says why when the keys cannot be moved", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    secretsApi.status.mockResolvedValue({
      local: true,
      mode: "file",
      available: true,
      backend: "Vault",
    })
    secretsApi.setMode.mockRejectedValue({ response: { data: { detail: "locked keychain" } } })
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.find("[data-setting='keychain'] .switch").trigger("click")
    await flushPromises()

    expect(wrapper.find("[data-keychain-error]").text()).toBe("locked keychain")
    expect(wrapper.find("[data-setting='keychain'] .switch").attributes("data-on")).toBe("false")
  })

  it("hides the keychain option when none is available or the server is another machine", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    for (const reply of [
      { local: true, mode: "file", available: false, backend: "" },
      { local: false, mode: "file", available: true, backend: "Vault" },
    ]) {
      secretsApi.status.mockResolvedValue(reply)
      const wrapper = mountSettings()
      await flushPromises()
      expect(wrapper.find("[data-setting='keychain']").exists()).toBe(false)
      wrapper.unmount()
    }
  })

  it("offers keep-running-in-the-tray only when the shell has a tray, and applies its answer", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const api = {
      get_close_to_tray: vi.fn(async () => false),
      set_close_to_tray: vi.fn(async () => true),
    }
    window.pywebview = { api }
    const wrapper = mountSettings()
    await flushPromises()

    const toggle = wrapper.find("[data-setting='close-to-tray'] .switch")
    expect(toggle.attributes("data-on")).toBe("false")
    await toggle.trigger("click")
    await flushPromises()
    expect(api.set_close_to_tray).toHaveBeenCalledWith(true)
    expect(wrapper.find("[data-setting='close-to-tray'] .switch").attributes("data-on")).toBe(
      "true",
    )

    api.set_close_to_tray.mockResolvedValue(false)
    await wrapper.find("[data-setting='close-to-tray'] .switch").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-setting='close-to-tray'] .switch").attributes("data-on")).toBe(
      "true",
    )
  })

  it("records a summon shortcut from a key press, reports a refusal, and clears it", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const api = {
      get_summon_shortcut: vi.fn(async () => ({ shortcut: null })),
      set_summon_shortcut: vi.fn(async () => true),
    }
    window.pywebview = { api }
    const wrapper = mountSettings()
    await flushPromises()

    const press = (init) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { cancelable: true, ...init }))
    await wrapper.find("[data-summon-record]").trigger("click")
    expect(wrapper.find("[data-summon-record]").text()).toBe("settings.desktop.summonRecording")
    press({ code: "KeyK" })
    expect(api.set_summon_shortcut).not.toHaveBeenCalled()
    press({ code: "Space", ctrlKey: true, shiftKey: true })
    await flushPromises()
    expect(api.set_summon_shortcut).toHaveBeenCalledWith("CommandOrControl+Shift+Space")
    expect(wrapper.find("[data-summon-record]").text()).toContain("Shift+Space")
    expect(wrapper.find("[data-summon-clear]").exists()).toBe(true)

    api.set_summon_shortcut.mockResolvedValue(false)
    await wrapper.find("[data-summon-record]").trigger("click")
    press({ code: "KeyJ", altKey: true })
    await flushPromises()
    expect(wrapper.find("[data-summon-error]").exists()).toBe(true)
    expect(wrapper.find("[data-summon-record]").text()).toContain("Shift+Space")

    api.set_summon_shortcut.mockResolvedValue(true)
    await wrapper.find("[data-summon-clear]").trigger("click")
    await flushPromises()
    expect(api.set_summon_shortcut).toHaveBeenLastCalledWith(null)
    expect(wrapper.find("[data-summon-clear]").exists()).toBe(false)

    await wrapper.find("[data-summon-record]").trigger("click")
    press({ code: "Escape" })
    await flushPromises()
    expect(wrapper.find("[data-summon-record]").text()).toBe("settings.desktop.summonSet")
    wrapper.unmount()
  })

  it("does not offer the tray option to a shell without one", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    window.pywebview = { api: { get_autostart: vi.fn(async () => false) } }
    const wrapper = mountSettings()
    await flushPromises()
    expect(wrapper.find("[data-setting='close-to-tray']").exists()).toBe(false)
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
    expect(wrapper.find("[data-setting='backend']").exists()).toBe(false)
  })

  it("shows the backend the Tauri shell manages and restarts it on request", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    const invoke = vi.fn(async (name) => {
      if (name === "get_backend_status") {
        return {
          state: "running",
          uptimeSec: 7500,
          restarts: 2,
          memoryBytes: 612 * 1024 * 1024,
        }
      }
      return name === "get_shell_settings" ? { restorePopouts: true } : true
    })
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountSettings()
    await flushPromises()

    const summary = wrapper.find("[data-backend-summary]").text()
    expect(summary).toContain("settings.desktop.backendState.running")
    expect(summary).toContain("612 MB")
    expect(summary).toContain("2h 5m")
    expect(summary).toContain('"count":2')

    await wrapper.find("[data-backend-restart]").trigger("click")
    expect(invoke).toHaveBeenCalledWith("restart_backend", {})
    await wrapper.find("[data-backend-logs]").trigger("click")
    expect(invoke).toHaveBeenCalledWith("open_logs", {})
    delete window.__TAURI_INTERNALS__
  })

  it("names the runtime, warns when the shipped one is newer, and asks twice before repairing", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    runtimeApi.getRuntime.mockResolvedValue({
      active: { version: "2.0.0.dev5" },
      bundled: "2.2.1.dev11",
      bundled_newer: true,
    })
    const invoke = vi.fn(async (name) =>
      name === "get_backend_status"
        ? { state: "running" }
        : name === "get_shell_settings"
          ? {}
          : true,
    )
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountSettings()
    await flushPromises()

    const summary = wrapper.find("[data-runtime-summary]").text()
    expect(summary).toContain("2.0.0.dev5")
    expect(summary).toContain("2.2.1.dev11")
    expect(wrapper.find("[data-runtime-newer]").exists()).toBe(true)

    await wrapper.find("[data-runtime-repair]").trigger("click")
    expect(invoke).not.toHaveBeenCalledWith("repair_runtime", expect.anything())
    expect(wrapper.find("[data-runtime-repair]").text()).toBe(
      "settings.desktop.runtimeRepairConfirm",
    )
    await wrapper.find("[data-runtime-repair]").trigger("click")
    expect(invoke).toHaveBeenCalledWith("repair_runtime", {})
    expect(wrapper.find("[data-runtime-repair]").text()).toBe("settings.desktop.runtimeRepair")
    delete window.__TAURI_INTERNALS__
  })

  it("exports diagnostics and says where the file went, or that it failed, and stays quiet on cancel", async () => {
    shimsApi.status.mockResolvedValue(status({ supported: false }))
    let answer = { ok: true, path: "C:/out/diag.zip", files: 6 }
    const invoke = vi.fn(async (name) => {
      if (name === "get_backend_status") return { state: "running" }
      if (name === "get_shell_settings") return {}
      return name === "export_diagnostics" ? answer : true
    })
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountSettings()
    await flushPromises()

    await wrapper.find("[data-diagnostics-export]").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-diagnostics-result]").text()).toContain("C:/out/diag.zip")

    answer = { ok: false, error: "disk full" }
    await wrapper.find("[data-diagnostics-export]").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-diagnostics-result]").text()).toBe(
      "settings.desktop.diagnosticsFailed",
    )

    answer = { ok: false, cancelled: true }
    await wrapper.find("[data-diagnostics-export]").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-diagnostics-result]").exists()).toBe(false)
    delete window.__TAURI_INTERNALS__
  })
})
