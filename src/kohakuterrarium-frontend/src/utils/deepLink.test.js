import { createPinia, setActivePinia } from "pinia"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useTabsStore } from "@/stores/tabs"
import { installDeepLinks, openDeepLink, parseDeepLink } from "@/utils/deepLink"

afterEach(() => {
  delete window.pywebview
  delete window.__TAURI_INTERNALS__
})

describe("parseDeepLink", () => {
  it("reads a saved-session link", () => {
    expect(parseDeepLink("kt://session/my-session")).toEqual({
      kind: "session",
      name: "my-session",
    })
    expect(parseDeepLink("kt://session/a%20b")).toEqual({ kind: "session", name: "a b" })
  })

  it("reads live-session, new-session and install links", () => {
    expect(parseDeepLink("kt://live/graph-1")).toEqual({ kind: "live", scope: "graph-1" })
    expect(parseDeepLink("kt://new")).toEqual({ kind: "new", path: "" })
    expect(parseDeepLink("kt://new?path=C%3A%5Cwork%5Crepo")).toEqual({
      kind: "new",
      path: "C:\\work\\repo",
    })
    expect(parseDeepLink(`kt://new?path=${"x".repeat(2000)}`)).toEqual({ kind: "new", path: "" })
    expect(parseDeepLink("kt://install/kt-biome")).toEqual({ kind: "install", spec: "@kt-biome" })
  })

  it("accepts only marketplace names for an install, never a url, path or option", () => {
    for (const bad of [
      "kt://install/",
      "kt://install/https%3A%2F%2Fevil.example%2Fx.git",
      "kt://install/a%2Fb",
      "kt://install/..",
      "kt://install/-e",
      "kt://install/x%20--editable",
      `kt://install/${"a".repeat(65)}`,
      "kt://live/",
      "kt://live/a/b",
    ]) {
      expect(parseDeepLink(bad), bad).toBeNull()
    }
  })

  it("rejects other schemes, hosts and unsafe names", () => {
    expect(parseDeepLink("https://session/x")).toBeNull()
    expect(parseDeepLink("kt://settings/x")).toBeNull()
    expect(parseDeepLink("kt://session/")).toBeNull()
    expect(parseDeepLink("kt://session/a/b")).toBeNull()
    expect(parseDeepLink("kt://session/..%5Cwin")).toBeNull()
    expect(parseDeepLink("not a url")).toBeNull()
    expect(parseDeepLink(undefined)).toBeNull()
  })
})

describe("openDeepLink", () => {
  it("asks the app to open the saved session", () => {
    const seen = []
    const listener = (event) => seen.push(event.detail.sessionName)
    window.addEventListener("kt:open-saved-session-history", listener)

    expect(openDeepLink("kt://session/abc")).toBe(true)
    expect(openDeepLink("kt://nope/abc")).toBe(false)

    window.removeEventListener("kt:open-saved-session-history", listener)
    expect(seen).toEqual(["abc"])
  })
})

describe("openDeepLink actions", () => {
  const collect = (name) => {
    const seen = []
    const listener = (event) => seen.push(event.detail)
    window.addEventListener(name, listener)
    return { seen, stop: () => window.removeEventListener(name, listener) }
  }

  it("opens the new-session dialog in a folder", () => {
    const watcher = collect("kt:open-workspace")
    expect(openDeepLink("kt://new?path=C%3A%5Crepo")).toBe(true)
    expect(openDeepLink("kt://new")).toBe(true)
    watcher.stop()
    expect(watcher.seen).toEqual([{ path: "C:\\repo" }, { path: "" }])
  })

  it("asks before installing and never installs by itself", () => {
    const watcher = collect("kt:deep-link-install")
    expect(openDeepLink("kt://install/kt-biome")).toBe(true)
    expect(openDeepLink("kt://install/https%3A%2F%2Fevil.example")).toBe(false)
    watcher.stop()
    expect(watcher.seen).toEqual([{ spec: "@kt-biome" }])
  })

  it("shows a running session in its tab", () => {
    setActivePinia(createPinia())
    expect(openDeepLink("kt://live/graph-7")).toBe(true)
    expect(useTabsStore().byId["attach:graph-7"]).toMatchObject({ target: "graph-7" })
  })
})

describe("installDeepLinks", () => {
  it("opens links the shell dispatches while the page is running", () => {
    const seen = []
    const listener = (event) => seen.push(event.detail.sessionName)
    window.addEventListener("kt:open-saved-session-history", listener)
    const stop = installDeepLinks()

    window.dispatchEvent(new CustomEvent("kt:deep-link", { detail: { url: "kt://session/live" } }))
    stop()
    window.dispatchEvent(new CustomEvent("kt:deep-link", { detail: { url: "kt://session/late" } }))

    window.removeEventListener("kt:open-saved-session-history", listener)
    expect(seen).toEqual(["live"])
  })

  it("opens a link that arrived before the page was ready", async () => {
    const invoke = vi.fn(async () => "kt://session/early")
    window.__TAURI_INTERNALS__ = { invoke }
    const seen = []
    const listener = (event) => seen.push(event.detail.sessionName)
    window.addEventListener("kt:open-saved-session-history", listener)

    const stop = installDeepLinks()
    await vi.waitFor(() => expect(seen).toEqual(["early"]))

    stop()
    window.removeEventListener("kt:open-saved-session-history", listener)
    expect(invoke).toHaveBeenCalledWith("take_deep_link", {})
  })
})
