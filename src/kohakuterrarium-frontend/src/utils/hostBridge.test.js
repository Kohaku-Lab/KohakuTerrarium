import { afterEach, describe, expect, it, vi } from "vitest"

import {
  DESKTOP_PROTOCOL,
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
