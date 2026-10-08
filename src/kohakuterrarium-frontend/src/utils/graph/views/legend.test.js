import { describe, expect, it } from "vitest"

import en from "@/utils/i18n/locales/en"
import zhCN from "@/utils/i18n/locales/zh-CN"
import zhTW from "@/utils/i18n/locales/zh-TW"
import { buildGraphModel } from "@/utils/graph/data/model"
import { DEFAULT_LAYERS, projectGraph } from "@/utils/graph/data/projection"

import { LEGEND_KEYS, legendKeys } from "./legend"

function creature(id, extra = {}) {
  return {
    creature_id: id,
    name: id,
    running: true,
    listen_channels: [],
    send_channels: [],
    ...extra,
  }
}

const model = buildGraphModel({
  graphs: [
    {
      graph_id: "g",
      creatures: [
        creature("root", { is_privileged: true, send_channels: ["in"], listen_channels: ["out"] }),
        creature("a", { listen_channels: ["in", "back"], send_channels: ["mid", "chat"] }),
        creature("b", { listen_channels: ["mid", "chat"], send_channels: ["out", "back", "chat"] }),
        creature("c", { listen_channels: ["mid"], send_channels: ["out"] }),
      ],
      output_edges: [
        { edge_id: "p", from: "b", to_creature_id: "root", with_content: false },
        { edge_id: "q", from: "c", to_creature_id: "a", with_content: false },
      ],
    },
  ],
})
const project = (layers = {}, channelMode = "node") =>
  projectGraph(model, { sessionId: "g", channelMode, layers: { ...DEFAULT_LAYERS, ...layers } })

describe("legend keys", () => {
  it("explains purple wherever a privileged link is drawn, and only there", () => {
    // The default Flow draws privileged links as chips, so purple arrows are not explained.
    expect(legendKeys("flow", project({}, "inline"))).toContain("privilegedChip")
    expect(legendKeys("flow", project({}, "inline"))).not.toContain("control")
    const flow = legendKeys("flow", project({}, "inline"), { privilegedLinks: "bundle" })
    expect(flow).toEqual(expect.arrayContaining(["handoff", "control", "bundle", "back", "group"]))
    expect(flow).not.toContain("membership")
    expect(flow).not.toContain("privilegedChip")
    expect(legendKeys("network", project())).toEqual(
      expect.arrayContaining(["membership", "control", "direct", "ping", "sendPort", "wirePort"]),
    )
    const listenOnly = legendKeys("network", project({ privilegedSend: false, direct: false }))
    expect(listenOnly).toContain("control")
    expect(listenOnly).not.toContain("direct")
    const none = project({ privilegedListen: false, privilegedSend: false, direct: false })
    expect(legendKeys("network", none)).not.toContain("control")
  })

  it("follows the Flow knob: chips instead of purple arrows, no bundle label per arrow", () => {
    const chips = legendKeys("flow", project({}, "inline"), { privilegedLinks: "chips" })
    expect(chips).toContain("privilegedChip")
    expect(chips).not.toContain("control")
    expect(chips).not.toContain("bundle")
    const pair = legendKeys("flow", project({}, "inline"), { privilegedLinks: "pair" })
    expect(pair).toContain("control")
    expect(pair).not.toContain("bundle")
  })

  it("lists the spawn tree and bus vocabulary for those views", () => {
    expect(legendKeys("tiers", project())).toEqual(["spawn", "wireChip"])
    expect(legendKeys("bus", project())).toEqual(["ping", "busListen", "busSend"])
  })

  it("has a legend string for every key in every graph locale", () => {
    for (const locale of [en, zhCN, zhTW])
      for (const key of LEGEND_KEYS) expect(locale[`graph.legend.${key}`], key).toBeTruthy()
  })
})
