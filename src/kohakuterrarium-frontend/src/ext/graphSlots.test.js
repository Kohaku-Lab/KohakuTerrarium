import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, inject } from "vue"

import GraphSlots from "@/ext/GraphSlots.vue"
import { badgesFor, graphBadges, graphNodeInfo, graphPanels, panelsFor } from "@/ext/graphSlots"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { GRAPH_NODE_INJECT_KEY } from "@/ext/sdk/keys"
import { extState, runtime } from "@/ext/state"

function declare(contributes) {
  const rec = { package: "alpha", enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
}

const slot = (kind, id, nodes, extra = {}) => ({
  kind,
  id,
  label: id,
  owner: "package:alpha",
  graph: { nodes },
  ...extra,
})

const Who = defineComponent({
  setup() {
    const node = inject(GRAPH_NODE_INJECT_KEY)
    return () =>
      h("span", { "data-test": "who" }, `${node.value.kind}:${node.value.id}:${node.value.name}`)
  },
})

describe("graph badges and panels", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  it("describes a node the same way for creatures, channels and groups", () => {
    expect(graphNodeInfo("creature", { id: "c1", name: "root", sessionId: "s" })).toEqual({
      kind: "creature",
      id: "c1",
      name: "root",
      sessionId: "s",
    })
    expect(graphNodeInfo("group", { key: "g1", label: "Hosts" })).toEqual({
      kind: "group",
      id: "g1",
      name: "Hosts",
      sessionId: null,
    })
    expect(graphNodeInfo("channel", { name: "tasks", sessionId: "s" }).id).toBe("tasks")
    expect(graphNodeInfo("creature", null)).toEqual({
      kind: "creature",
      id: "",
      name: "",
      sessionId: null,
    })
  })

  it("registers badges and panels by node kind, ordered, and clears them", () => {
    declare([
      slot("graph-badge", "b2", ["creature"], { order: 2 }),
      slot("graph-badge", "b1", ["creature", "channel"], { order: 1 }),
      slot("graph-panel", "p", ["group"]),
    ])
    expect(badgesFor("creature").map((b) => b.id)).toEqual(["b1", "b2"])
    expect(badgesFor("channel").map((b) => b.id)).toEqual(["b1"])
    expect(badgesFor("group")).toEqual([])
    expect(panelsFor("group").map((p) => p.id)).toEqual(["p"])
    expect(panelsFor("creature")).toEqual([])

    unregisterAllContributions()
    expect(graphBadges.size + graphPanels.size).toBe(0)
  })

  it("defaults a code-registered badge to every node kind", () => {
    extState.packages = [{ package: "alpha", enabled: true, status: "idle", contributes: [] }]
    provideContributionComponent("alpha", "graph-badge", "any", Who)
    expect(["creature", "channel", "group"].every((k) => badgesFor(k).length === 1)).toBe(true)
  })

  it("draws a badge with the read-only node and nothing for another kind", async () => {
    declare([slot("graph-badge", "who", ["creature"])])
    provideContributionComponent("alpha", "graph-badge", "who", Who)
    const wrapper = mount(GraphSlots, {
      props: { variant: "badge", kind: "creature", item: { id: "c1", name: "root" } },
    })
    await vi.waitFor(() => expect(wrapper.find('[data-test="who"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="who"]').text()).toBe("creature:c1:root")

    const other = mount(GraphSlots, {
      props: { variant: "badge", kind: "channel", item: { name: "tasks" } },
    })
    expect(other.find('[data-test="graph-badges"]').exists()).toBe(false)
  })

  it("draws a panel with its label as the title", async () => {
    declare([slot("graph-panel", "who", ["group"], { label: "Overview" })])
    provideContributionComponent("alpha", "graph-panel", "who", Who)
    const wrapper = mount(GraphSlots, {
      props: { variant: "panel", kind: "group", item: { key: "g1", label: "Hosts" } },
    })
    await vi.waitFor(() => expect(wrapper.find('[data-test="who"]').exists()).toBe(true))
    expect(wrapper.text()).toContain("Overview")
    expect(wrapper.find('[data-test="who"]').text()).toBe("group:g1:Hosts")
  })
})
