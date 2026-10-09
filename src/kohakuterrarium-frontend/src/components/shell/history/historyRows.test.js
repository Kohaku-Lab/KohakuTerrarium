import { describe, expect, it } from "vitest"

import {
  baseName,
  historyRow,
  originNode,
  resumeNode,
  sessionStatus,
  shortId,
  whenLabel,
} from "./historyRows"

const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

describe("history rows", () => {
  it("labels by name, then summary, then recipe with a short id", () => {
    const base = {
      name: "pair_3d736342ab",
      terrarium_name: "pair",
      agents: ["a", "b"],
      preview: "older prompt",
      last_user: "latest prompt",
    }
    const named = historyRow({ ...base, title: "Nightly triage", summary: "Triaging CI" })
    expect(named).toMatchObject({
      label: "Nightly triage",
      labelFrom: "title",
      recipe: "pair",
      shortId: "",
      line: "Triaging CI",
    })
    const summarized = historyRow({ ...base, summary: "Triaging CI" })
    expect(summarized).toMatchObject({
      label: "Triaging CI",
      labelFrom: "summary",
      line: "latest prompt",
    })
    const bare = historyRow(base)
    expect(bare).toMatchObject({
      label: "pair",
      labelFrom: "recipe",
      shortId: "3d736342",
      line: "latest prompt",
    })
    expect(historyRow({ name: "solo" })).toMatchObject({ label: "solo", shortId: "", line: "" })
    expect(historyRow({ name: "x", preview: "p" }).line).toBe("p")
    const cut = historyRow({
      name: "x",
      summary: "Refactor the session index…",
      last_user: "Refactor the session  index reconcile loop",
    })
    expect(cut.line).toBe("")
    expect(shortId("probe")).toBe("probe")
  })

  it("reads the status from stop reason and lifecycle", () => {
    expect(sessionStatus({ stop_reason: "crash" })).toBe("crashed")
    expect(sessionStatus({ stop_reason: "shutdown", lifecycle: { live: true } })).toBe("shutdown")
    expect(sessionStatus({ stop_reason: null, lifecycle: { live: true } })).toBe("running")
    expect(sessionStatus({ stop_reason: "user", lifecycle: { live: false } })).toBe("")
    expect(sessionStatus({})).toBe("")
    expect(
      historyRow({ name: "s", turn_count: 4, last_reply: " ok ", stop_reason: "crash" }),
    ).toMatchObject({ turns: 4, lastReply: "ok", status: "crashed" })
  })

  it("keeps the session's shape: config, members, folder, forks", () => {
    const row = historyRow({
      name: "team_ab12",
      terrarium_name: "My team",
      config_type: "terrarium",
      config_path: "@kt-biome/terrariums/swe_team/",
      agents: ["a", "b", "c"],
      pwd: "C:\\work\\project",
      preview: [{ type: "text", text: "fix the bug" }],
      forked_children: ["x", "y"],
      has_vector_index: true,
    })
    expect(row).toMatchObject({
      key: "team_ab12",
      label: "My team",
      shortId: "ab12",
      config: "swe_team",
      agents: 3,
      pwdName: "project",
      preview: "fix the bug",
      forks: 2,
      hasVectorIndex: true,
    })
    expect(historyRow({ name: "solo", config_type: "agent" })).toMatchObject({
      label: "solo",
      shortId: "",
      config: "",
      agents: 0,
      preview: "",
    })
  })

  it("resumes on the picked machine, else where it last ran, else the host", () => {
    expect(originNode({ on_node: "w1", home_node: "w2" })).toBe("w1")
    expect(originNode({ home_node: "w2" })).toBe("w2")
    expect(resumeNode({ node_id: "w3" }, "")).toBe("w3")
    expect(resumeNode({ node_id: "w3" }, "w9")).toBe("w9")
    expect(resumeNode({}, "")).toBe("_host")
    expect(baseName("/a/b/")).toBe("b")
    expect(baseName("")).toBe("")
  })

  it("says when by calendar day, not by elapsed hours", () => {
    const now = new Date(2026, 9, 9, 0, 30)
    expect(whenLabel(new Date(2026, 9, 8, 23, 50).toISOString(), t, now)).toBe("sessions.yesterday")
    expect(whenLabel(new Date(2026, 9, 9, 0, 10).toISOString(), t, now)).not.toContain("sessions.")
    expect(whenLabel(new Date(2026, 9, 5, 12).toISOString(), t, now)).toBe(
      'sessions.daysAgo:{"count":4}',
    )
    expect(whenLabel(new Date(2026, 8, 1).toISOString(), t, now)).not.toContain("sessions.")
    expect(whenLabel("", t, now)).toBe("")
    expect(whenLabel("not a date", t, now)).toBe("")
  })
})
