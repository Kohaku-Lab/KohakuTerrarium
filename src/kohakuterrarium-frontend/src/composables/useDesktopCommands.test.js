import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { DESKTOP_COMMAND_IDS, useDesktopCommands } from "@/composables/useDesktopCommands"
import { usePaletteStore } from "@/stores/palette"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

afterEach(() => {
  delete window.__TAURI_INTERNALS__
})

describe("useDesktopCommands", () => {
  it("adds nothing in a browser", () => {
    useDesktopCommands()
    expect(usePaletteStore().commands).toHaveLength(0)
  })

  it("adds the shell's actions in the Tauri shell and each one calls its command", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    useDesktopCommands()
    const commands = usePaletteStore().commands
    expect(commands.map((command) => command.id).sort()).toEqual(
      Object.values(DESKTOP_COMMAND_IDS).sort(),
    )
    const run = (id) => commands.find((command) => command.id === id).handler()

    run(DESKTOP_COMMAND_IDS.restart)
    expect(invoke).toHaveBeenLastCalledWith("restart_backend", {})
    run(DESKTOP_COMMAND_IDS.logs)
    expect(invoke).toHaveBeenLastCalledWith("open_logs", {})
    run(DESKTOP_COMMAND_IDS.diagnostics)
    expect(invoke).toHaveBeenLastCalledWith("export_diagnostics", {})
    run(DESKTOP_COMMAND_IDS.settings)
    expect(useTabsStore().byId.settings).toBeTruthy()
  })
})
