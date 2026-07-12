/**
 * DriveDetail mounted interaction tests (coverage-and-verification §Product
 * surfaces). Pins R1-38:
 *
 *  - the "Wake" control invokes the dedicated wake op (emits ``wake``), never a
 *    generic transition to ``waiting``;
 *  - the progress + audit timelines render from ``detail.progress`` /
 *    ``detail.audit`` (loaded separately from the record).
 */

import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"
import ElementPlus from "element-plus"

import DriveDetail from "./DriveDetail.vue"

function _rec(extra = {}) {
  return {
    drive_id: "d1",
    kind: "generic",
    revision: 3,
    title: "Ship it",
    status: "waiting",
    status_reason: null,
    scope_type: "graph",
    scope_id: "g1",
    priority: 0,
    owner: "user:alice",
    owner_scope: "actor",
    created_by: "user:alice",
    created_at: "2026-07-01T00:00:00+00:00",
    updated_at: "2026-07-01T00:00:00+00:00",
    assignee_creature_id: null,
    assignment_state: "unassigned",
    availability: "available",
    durability: "persistent",
    spec: {},
    presentation: {},
    metadata: {},
    ...extra,
  }
}

function mountDetail(props) {
  return mount(DriveDetail, {
    props: { record: _rec(), allowedActions: ["read", "transition"], ...props },
    global: { plugins: [ElementPlus] },
  })
}

function buttonByText(w, text) {
  return w.findAll("button").find((b) => b.text().trim() === text)
}

describe("DriveDetail — R1-38 wake wiring", () => {
  it("a WAITING drive's Wake button emits wake, not a transition", async () => {
    const w = mountDetail({ record: _rec({ status: "waiting" }) })
    const wake = buttonByText(w, "Wake")
    expect(wake).toBeTruthy()
    await wake.trigger("click")
    expect(w.emitted("wake")).toBeTruthy()
    // It must NOT fall back to the generic transition path.
    expect(w.emitted("transition")).toBeFalsy()
  })

  it("shows Resume (not Wake) for a paused drive", () => {
    const w = mountDetail({ record: _rec({ status: "paused" }) })
    expect(buttonByText(w, "Wake")).toBeFalsy()
    expect(buttonByText(w, "Resume")).toBeTruthy()
  })
})

describe("DriveDetail — R1-38 timeline sections", () => {
  it("renders progress and audit from the detail payload", () => {
    const w = mountDetail({
      record: _rec({ status: "active" }),
      detail: {
        progress: [
          {
            progress_id: "p1",
            actor: "user:alice",
            summary: "did step one",
            created_at: "2026-07-02T00:00:00+00:00",
          },
        ],
        audit: [
          {
            audit_id: "a1",
            operation: "create",
            actor: "user:alice",
            created_at: "2026-07-01T00:00:00+00:00",
          },
        ],
      },
    })
    const text = w.text()
    expect(text).toContain("did step one")
    expect(text).toContain("Progress")
    expect(text).toContain("create")
    expect(text).toContain("Recent audit")
  })

  it("hides the timelines when detail carries no progress/audit", () => {
    const w = mountDetail({
      record: _rec({ status: "active" }),
      detail: { progress: [], audit: [] },
    })
    const text = w.text()
    expect(text).not.toContain("Recent audit")
  })
})
