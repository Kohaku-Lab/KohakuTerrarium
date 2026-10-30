import { afterEach, describe, expect, it, vi } from "vitest"

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
