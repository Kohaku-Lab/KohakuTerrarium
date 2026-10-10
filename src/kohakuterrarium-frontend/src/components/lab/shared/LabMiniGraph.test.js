import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import LabMiniGraph from "./LabMiniGraph.vue"
import { buildSessions } from "@/components/lab/model/labSessions"
import { buildGraphModel } from "@/utils/graph/data/model"

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})
const session = buildSessions(
  buildGraphModel({
    graphs: [
      {
        graph_id: "g",
        channels: [{ name: "tasks" }],
        creatures: [
          creature("lead", {
            is_privileged: true,
            send_channels: ["tasks"],
            listen_channels: ["tasks"],
          }),
          creature("coder", { send_channels: ["tasks"], is_processing: true }),
          creature("reviewer", { listen_channels: ["tasks"], running: false }),
        ],
      },
    ],
  }),
)[0]

describe("LabMiniGraph", () => {
  it("draws each creature with its status bar and label, privileged edged, and each channel as a labelled pill", () => {
    const w = mount(LabMiniGraph, { props: { session } })
    const node = (id) => w.find(`[data-test='lab-mini-node-${id}']`)
    expect(node("lead").find(".kt-lab-mini-node--privileged").exists()).toBe(true)
    expect(node("coder").find(".kt-lab-mini-node--privileged").exists()).toBe(false)
    expect(node("coder").find(".kt-lab-mini-bar--busy").exists()).toBe(true)
    expect(node("reviewer").find(".kt-lab-mini-bar--stopped").exists()).toBe(true)
    expect(node("reviewer").find("text").text()).toBe("reviewer")
    expect(w.find(".kt-lab-mini-channel-label").text()).toBe("tasks")
    expect(w.findAll(".kt-lab-mini-link")).toHaveLength(3)
    expect(w.findAll(".kt-lab-mini-link--control")).toHaveLength(1)
  })

  it("moves message dots only while active, and only along worker links that send", async () => {
    const w = mount(LabMiniGraph, { props: { session } })
    expect(w.findAll("circle")).toHaveLength(0)
    await w.setProps({ active: true })
    expect(w.findAll("circle")).toHaveLength(1)
  })
})
