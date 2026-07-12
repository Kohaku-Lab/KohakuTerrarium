import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import DriveCountBadges from "./DriveCountBadges.vue"

describe("DriveCountBadges", () => {
  it("hides zero-count badges by default", () => {
    const w = mount(DriveCountBadges, {
      props: { counts: { active: 2, blocked: 0, deadLetter: 0 } },
    })
    const text = w.text()
    expect(text).toContain("2 active")
    expect(text).not.toContain("blocked")
    expect(text).not.toContain("dead-letter")
  })

  it("renders nothing when every count is zero", () => {
    const w = mount(DriveCountBadges, { props: { counts: {} } })
    expect(w.text().trim()).toBe("")
  })

  it("surfaces blocked + dead-letter counts", () => {
    const w = mount(DriveCountBadges, { props: { counts: { blocked: 1, deadLetter: 3 } } })
    const text = w.text()
    expect(text).toContain("1 blocked")
    expect(text).toContain("3 dead-letter")
  })

  it("emits badge-click only when clickable", async () => {
    const w = mount(DriveCountBadges, { props: { counts: { active: 1 }, clickable: true } })
    await w.find("button").trigger("click")
    expect(w.emitted("badge-click")[0]).toEqual(["active"])
  })
})
