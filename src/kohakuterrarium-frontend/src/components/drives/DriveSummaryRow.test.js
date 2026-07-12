import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import DriveSummaryRow from "./DriveSummaryRow.vue"

function _rec(extra = {}) {
  return {
    drive_id: "d1",
    kind: "generic",
    title: "Ship the thing",
    status: "blocked",
    owner: "user:alice",
    assignee_creature_id: "worker-1",
    assignment_state: "assigned",
    availability: "available",
    priority: 3,
    ...extra,
  }
}

describe("DriveSummaryRow", () => {
  it("renders the status label, owner→assignee, and priority", () => {
    const w = mount(DriveSummaryRow, { props: { record: _rec() } })
    const text = w.text()
    expect(text).toContain("Ship the thing")
    expect(text).toContain("Blocked")
    expect(text).toContain("alice")
    expect(text).toContain("worker-1")
    expect(text).toContain("p3")
  })

  it("shows a dead-letter badge from delivery flags", () => {
    const w = mount(DriveSummaryRow, {
      props: { record: _rec({ status: "active" }), flags: { deadLetter: true } },
    })
    expect(w.text()).toContain("Dead-letter")
  })

  it("shows an orphaned badge and an unavailable badge", () => {
    const w = mount(DriveSummaryRow, {
      props: {
        record: _rec({ assignment_state: "orphaned", availability: "registration_disabled" }),
      },
    })
    const text = w.text()
    expect(text).toContain("Orphaned")
    expect(text).toContain("Registration disabled")
  })

  it("emits select with the drive id on click", async () => {
    const w = mount(DriveSummaryRow, { props: { record: _rec() } })
    await w.find("button").trigger("click")
    expect(w.emitted("select")).toBeTruthy()
    expect(w.emitted("select")[0]).toEqual(["d1"])
  })

  it("falls back to unassigned when there is no assignee", () => {
    const w = mount(DriveSummaryRow, { props: { record: _rec({ assignee_creature_id: null }) } })
    expect(w.text()).toContain("unassigned")
  })
})
