import { afterEach, describe, expect, it, vi } from "vitest"

import {
  DESKTOP_PROTOCOL,
  ackOverlayWindow,
  ackPopoutWindow,
  checkShellUpdate,
  openPopoutWindow,
  popoutWindowsSupported,
  closeOverlayWindow,
  getCloseToTray,
  getSummonShortcut,
  openOverlayWindow,
  setSummonShortcut,
  setAttentionCount,
  setCloseToTray,
  overlayWindowsSupported,
  setOverlayBounds,
  setOverlayHitRegions,
  startOverlayDrag,
  installShellUpdate,
  desktopCapabilities,
  getAutostart,
  hasDesktopHost,
  hostKind,
  hostSupports,
  onHostReady,
  openExternalViaHost,
  requestDesktopAttention,
  setAutostart,
  showDesktopNotification,
  takeDeepLink,
} from "@/utils/hostBridge"

afterEach(() => {
  delete window.pywebview
  delete window.__TAURI_INTERNALS__
})

describe("hostBridge", () => {
  it("reports no host in a plain browser and degrades every operation", async () => {
    expect(hostKind()).toBeNull()
    expect(hasDesktopHost()).toBe(false)
    expect(hostSupports("open_external")).toBe(false)
    expect(await desktopCapabilities()).toBeNull()
    expect(await requestDesktopAttention()).toBe(false)
    expect(await openExternalViaHost("https://example.test")).toBe(false)
  })

  it("routes calls to pywebview with positional arguments", async () => {
    const api = {
      get_desktop_capabilities: vi.fn(async () => ({
        surface: "desktop",
        protocol: DESKTOP_PROTOCOL,
        nativeAttention: true,
      })),
      request_desktop_attention: vi.fn(async () => true),
      open_external: vi.fn(async () => true),
    }
    window.pywebview = { api }

    expect(hostKind()).toBe("pywebview")
    expect((await desktopCapabilities()).nativeAttention).toBe(true)
    expect(await requestDesktopAttention()).toBe(true)
    expect(await openExternalViaHost("https://example.test/a")).toBe(true)
    expect(api.open_external).toHaveBeenCalledWith("https://example.test/a")
  })

  it("routes calls to Tauri through invoke with named arguments", async () => {
    const invoke = vi.fn(async (name) => (name === "open_external" ? true : null))
    window.__TAURI_INTERNALS__ = { invoke }

    expect(hostKind()).toBe("tauri")
    expect(await openExternalViaHost("https://example.test/b")).toBe(true)
    expect(invoke).toHaveBeenCalledWith("open_external", { args: ["https://example.test/b"] })
  })

  it("ignores capabilities from another protocol version or surface", async () => {
    window.pywebview = {
      api: { get_desktop_capabilities: async () => ({ surface: "desktop", protocol: 99 }) },
    }
    expect(await desktopCapabilities()).toBeNull()
    window.pywebview = {
      api: { get_desktop_capabilities: async () => ({ surface: "web", protocol: 1 }) },
    }
    expect(await desktopCapabilities()).toBeNull()
  })

  it("treats a failing shell call as unavailable", async () => {
    window.pywebview = {
      api: {
        get_desktop_capabilities: async () => {
          throw new Error("boom")
        },
        request_desktop_attention: async () => {
          throw new Error("boom")
        },
        open_external: async () => {
          throw new Error("boom")
        },
      },
    }
    expect(await desktopCapabilities()).toBeNull()
    expect(await requestDesktopAttention()).toBe(false)
    expect(await openExternalViaHost("https://example.test")).toBe(false)
  })

  it("only counts an exact true as success", async () => {
    window.pywebview = { api: { open_external: async () => "yes" } }
    expect(await openExternalViaHost("https://example.test")).toBe(false)
  })

  it("does not call a method the shell lacks", async () => {
    window.pywebview = { api: {} }
    expect(hostSupports("open_external")).toBe(false)
    expect(await openExternalViaHost("https://example.test")).toBe(false)
  })

  it("shows notifications and reads or changes autostart through the shell", async () => {
    const api = {
      show_notification: vi.fn(async () => true),
      get_autostart: vi.fn(async () => true),
      set_autostart: vi.fn(async () => true),
      take_deep_link: vi.fn(async () => "kt://session/x"),
    }
    window.pywebview = { api }

    expect(await showDesktopNotification("Title", "Body")).toBe(true)
    expect(api.show_notification).toHaveBeenCalledWith("Title", "Body")
    expect(await getAutostart()).toBe(true)
    expect(await setAutostart(false)).toBe(false)
    expect(api.set_autostart).toHaveBeenCalledWith(false)
    expect(await takeDeepLink()).toBe("kt://session/x")
  })

  it("checks for and installs a shell update through the shell", async () => {
    const api = {
      check_shell_update: vi.fn(async () => ({ available: true, version: "9.9.9" })),
      install_shell_update: vi.fn(async () => true),
    }
    window.pywebview = { api }

    expect(await checkShellUpdate()).toEqual({ available: true, version: "9.9.9" })
    expect(await installShellUpdate()).toBe(true)
  })

  it("treats a shell without updates or a failing update as nothing to do", async () => {
    window.pywebview = { api: {} }
    expect(await checkShellUpdate()).toBeNull()
    expect(await installShellUpdate()).toBe(false)
    window.pywebview = {
      api: {
        check_shell_update: async () => {
          throw new Error("offline")
        },
        install_shell_update: async () => {
          throw new Error("offline")
        },
      },
    }
    expect(await checkShellUpdate()).toBeNull()
    expect(await installShellUpdate()).toBe(false)
  })

  it("reports the state the shell actually reached when it refuses a change", async () => {
    window.pywebview = { api: { set_autostart: vi.fn(async () => false) } }

    expect(await setAutostart(true)).toBe(false)
  })

  it("answers null or false for the optional operations of a shell without them", async () => {
    window.pywebview = { api: {} }

    expect(await showDesktopNotification("a", "b")).toBe(false)
    expect(await getAutostart()).toBeNull()
    expect(await setAutostart(true)).toBeNull()
    expect(await takeDeepLink()).toBeNull()
  })

  it("reads and changes close-to-tray and sends attention counts", async () => {
    const api = {
      get_close_to_tray: vi.fn(async () => true),
      set_close_to_tray: vi.fn(async () => true),
      set_attention_count: vi.fn(async () => true),
    }
    window.pywebview = { api }
    expect(await getCloseToTray()).toBe(true)
    expect(await setCloseToTray(false)).toBe(false)
    expect(api.set_close_to_tray).toHaveBeenCalledWith(false)
    expect(await setAttentionCount(2.9, -4)).toBe(true)
    expect(api.set_attention_count).toHaveBeenCalledWith(2, 0)

    api.set_close_to_tray.mockResolvedValue(false)
    expect(await setCloseToTray(true)).toBe(false)
    window.pywebview = { api: {} }
    expect(await getCloseToTray()).toBeNull()
    expect(await setCloseToTray(true)).toBeNull()
    expect(await setAttentionCount(1, 1)).toBe(false)
  })

  it("opens session windows through Tauri only", async () => {
    expect(popoutWindowsSupported()).toBe(false)
    expect(await openPopoutWindow({ label: "popout-a", scope: "s" })).toBe(false)
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    expect(popoutWindowsSupported()).toBe(true)
    expect(await openPopoutWindow({ label: "popout-a", scope: "s" })).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("open_popout_window", {
      args: [{ label: "popout-a", scope: "s" }],
    })
    expect(await ackPopoutWindow()).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("popout_ack", {})
    delete window.__TAURI_INTERNALS__
    window.pywebview = { api: { open_popout_window: async () => true } }
    expect(popoutWindowsSupported()).toBe(false)
  })

  it("reads, sets and clears the summon shortcut", async () => {
    const api = {
      get_summon_shortcut: vi.fn(async () => ({ shortcut: "Alt+K" })),
      set_summon_shortcut: vi.fn(async () => true),
    }
    window.pywebview = { api }
    expect(await getSummonShortcut()).toBe("Alt+K")
    expect(await setSummonShortcut("Alt+J")).toBe(true)
    expect(api.set_summon_shortcut).toHaveBeenLastCalledWith("Alt+J")
    expect(await setSummonShortcut(null)).toBe(true)
    expect(api.set_summon_shortcut).toHaveBeenLastCalledWith(null)
    api.get_summon_shortcut.mockResolvedValue({ shortcut: null })
    expect(await getSummonShortcut()).toBeNull()
    window.pywebview = { api: {} }
    expect(await getSummonShortcut()).toBeNull()
    expect(await setSummonShortcut("Alt+J")).toBe(false)
  })

  it("drives native overlay windows through Tauri only", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    expect(overlayWindowsSupported()).toBe(true)
    const spec = { label: "overlay-a", key: "ext:p/o", width: 100, height: 80 }
    expect(await openOverlayWindow(spec)).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("open_overlay_window", { args: [spec] })
    expect(await closeOverlayWindow("overlay-a")).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("close_overlay_window", { args: ["overlay-a"] })
    expect(await setOverlayBounds(1, 2, 3, 4)).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("overlay_set_bounds", { args: [1, 2, 3, 4] })
    expect(await setOverlayHitRegions([[0, 0, 5, 5]])).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("overlay_set_hit_regions", { args: [[[0, 0, 5, 5]]] })
    expect(await startOverlayDrag()).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("overlay_start_drag", {})
    expect(await ackOverlayWindow()).toBe(true)

    delete window.__TAURI_INTERNALS__
    window.pywebview = { api: { open_overlay_window: async () => true } }
    expect(overlayWindowsSupported()).toBe(false)
    window.__TAURI_INTERNALS__ = {
      invoke: async () => {
        throw new Error("denied")
      },
    }
    delete window.pywebview
    expect(await openOverlayWindow(spec)).toBe(false)
    delete window.__TAURI_INTERNALS__
    expect(await openOverlayWindow(spec)).toBe(false)
  })

  it("onHostReady fires immediately when hosted and again on pywebviewready", () => {
    const listener = vi.fn()
    window.pywebview = { api: {} }
    const stop = onHostReady(listener)
    expect(listener).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new Event("pywebviewready"))
    expect(listener).toHaveBeenCalledTimes(2)
    stop()
    window.dispatchEvent(new Event("pywebviewready"))
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it("onHostReady waits when no host is present yet", () => {
    const listener = vi.fn()
    const stop = onHostReady(listener)
    expect(listener).not.toHaveBeenCalled()
    window.pywebview = { api: {} }
    window.dispatchEvent(new Event("pywebviewready"))
    expect(listener).toHaveBeenCalledTimes(1)
    stop()
  })
})
