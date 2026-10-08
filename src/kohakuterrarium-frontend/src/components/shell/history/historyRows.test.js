import { describe, expect, it } from "vitest"

import { baseName, historyRow, originNode, resumeNode, whenLabel } from "./historyRows"

const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

describe("history rows", () => {
  it("shows the user-facing name, the storage key only when it differs, and the session's shape", () => {
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
      showKey: true,
      config: "swe_team",
      agents: 3,
      pwdName: "project",
      preview: "fix the bug",
      forks: 2,
      hasVectorIndex: true,
    })
    expect(historyRow({ name: "solo", config_type: "agent" })).toMatchObject({
      label: "solo",
      showKey: false,
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
