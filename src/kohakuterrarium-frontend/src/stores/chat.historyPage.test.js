import { describe, expect, it } from "vitest"

import { _convertHistory } from "./chat"

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
