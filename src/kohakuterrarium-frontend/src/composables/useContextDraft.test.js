import { nextTick, ref } from "vue"
import { describe, expect, it } from "vitest"

import { useContextDraft } from "./useContextDraft"

describe("useContextDraft", () => {
  it("shows the default unpinned, so nothing is sent until the user sets a length", () => {
    const draft = useContextDraft(ref(272000), ref(400000))
    draft.start("")
    expect(draft.text.value).toBe("272k")
    expect(draft.pinned.value).toBe(false)
    expect(draft.override.value).toBe(null)
    expect(draft.exceeds.value).toBe(false)
  })

  it("pins a typed or picked length and warns above the window without blocking it", () => {
    const draft = useContextDraft(ref(272000), ref(400000))
    draft.start("")
    draft.edit("1.5m")
    expect(draft.override.value).toBe(1500000)
    expect(draft.exceeds.value).toBe(true)
    expect(draft.invalid.value).toBe(false)
    draft.pick(200000)
    expect(draft.text.value).toBe("200k")
    expect(draft.override.value).toBe(200000)
    expect(draft.exceeds.value).toBe(false)
  })

  it("treats a cleared field as the preset default and junk as invalid", () => {
    const draft = useContextDraft(ref(272000), ref(null))
    draft.start("500k")
    expect(draft.pinned.value).toBe(true)
    expect(draft.override.value).toBe(500000)
    draft.edit("lots")
    expect(draft.invalid.value).toBe(true)
    expect(draft.override.value).toBe(null)
    draft.clear()
    expect(draft.pinned.value).toBe(false)
    expect(draft.invalid.value).toBe(false)
    expect(draft.override.value).toBe(null)
  })

  it("follows a changing default only while unpinned", async () => {
    const fallback = ref(272000)
    const draft = useContextDraft(fallback, ref(null))
    draft.start("")
    fallback.value = 1000000
    await nextTick()
    expect(draft.text.value).toBe("1m")
    draft.edit("300k")
    fallback.value = 128000
    await nextTick()
    expect(draft.text.value).toBe("300k")
  })

  it("offers the model window among the quick picks", () => {
    const draft = useContextDraft(ref(0), ref(128000))
    expect(draft.suggestions.value).toEqual([128000, 200000, 272000, 400000, 1000000])
    expect(draft.window.value).toBe(128000)
  })
})
