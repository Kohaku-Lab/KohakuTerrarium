import { afterEach, describe, expect, it, vi } from "vitest"

import {
  patchShellSetting,
  refreshShellSettings,
  resetShellSettings,
  useShellSettings,
} from "@/composables/useShellSettings"

afterEach(() => {
  delete window.__TAURI_INTERNALS__
  resetShellSettings()
})

function shell(initial) {
  let stored = { ...initial }
  const invoke = vi.fn(async (name, payload) => {
    if (name === "get_shell_settings") return stored
    const patch = payload.args[0]
    if ("bad" in patch) return { ok: false, error: "unknown setting bad" }
    stored = { ...stored, ...patch }
    return { ok: true, settings: stored }
  })
  window.__TAURI_INTERNALS__ = { invoke }
  return invoke
}

describe("useShellSettings", () => {
  it("stays empty outside the Tauri shell", async () => {
    const { settings } = useShellSettings()
    expect(await refreshShellSettings()).toBeNull()
    expect(settings.value).toBeNull()
    expect(await patchShellSetting({ keepAwake: "never" })).toBe(false)
  })

  it("shares one read and updates it when a patch is accepted", async () => {
    const invoke = shell({ keepAwake: "running", startHidden: false })
    const { settings } = useShellSettings()
    useShellSettings()
    await refreshShellSettings()
    expect(settings.value.keepAwake).toBe("running")
    expect(invoke.mock.calls.filter(([name]) => name === "get_shell_settings")).toHaveLength(1)

    expect(await patchShellSetting({ startHidden: true })).toBe(true)
    expect(settings.value).toEqual({ keepAwake: "running", startHidden: true })
  })

  it("keeps the old values when the shell refuses a patch", async () => {
    shell({ keepAwake: "running" })
    const { settings } = useShellSettings()
    await refreshShellSettings()
    expect(await patchShellSetting({ bad: 1 })).toBe(false)
    expect(settings.value).toEqual({ keepAwake: "running" })
  })
})
