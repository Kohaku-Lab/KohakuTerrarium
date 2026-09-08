import { createPinia, setActivePinia } from "pinia"
import { describe, expect, it } from "vitest"

import { _convertHistory, useChatStore } from "./chat"

describe("_convertHistory paged snapshot mode", () => {
  it("defaults to the legacy positional ids", () => {
    const out = _convertHistory([{ role: "user", content: "q" }])
    expect(out[0].id).toBe("h_0")
    expect(out[0]._historyKey).toBeUndefined()
  })

  it("keys a paged snapshot row by its stable _history_key, not position", () => {
    const out = _convertHistory(
      [
        { role: "user", content: "q", _history_key: "abs_10" },
        { role: "assistant", content: "a", _history_key: "abs_11" },
      ],
      { paged: true },
    )
    expect(out[0].id).toBe("abs_10")
    expect(out[0]._historyKey).toEqual(["abs_10"])
    expect(out[1].id).toBe("abs_11")
  })

  it("falls back to an absolute index when a paged record has no stable key", () => {
    const out = _convertHistory([{ role: "user", content: "q" }], { paged: true })
    expect(out[0]._historyKey).toEqual(["abs_0"])
  })
})

describe("per-store maps keyed by the store, not the action receiver", () => {
  it("resolves one paged controller per tab for any receiver", () => {
    setActivePinia(createPinia())
    const chat = useChatStore("receivers")
    const opts = { kind: "saved", sessionName: "saved" }
    const first = chat._controllerForTab("root", opts)
    // Pinia's devtools plugin invokes options-API actions with a fresh
    // Proxy of the store as ``this``; ownership must not follow it.
    const proxy = new Proxy(chat, {})
    expect(chat._controllerForTab.call(proxy, "root", opts)).toBe(first)
    chat.historyPageByTab.root = { historyId: "h1" }
    chat._disposeHistoryPageControllers.call(proxy)
    expect(chat.historyPageByTab).toEqual({})
    expect(chat._controllerForTab("root", opts)).not.toBe(first)
    chat._cleanup()
  })

  it("keeps the tool index visible to later actions", () => {
    setActivePinia(createPinia())
    const chat = useChatStore("tool-index")
    const part = { type: "tool", jobId: "job-1", name: "read", status: "running", id: "tool_1" }
    chat._indexToolPart("root", part)
    expect(chat._findToolPart.call(new Proxy(chat, {}), "root", [], "read", "job-1")).toBe(part)
    chat._cleanup()
  })
})
