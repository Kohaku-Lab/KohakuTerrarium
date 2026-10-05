/**
 * CreaturesPanel: selecting a channel tab splits the creature list into
 * members (send or listen) and non-members; any other tab shows the flat list.
 */

import { mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { nextTick, reactive } from "vue"

import CreaturesPanel from "./CreaturesPanel.vue"
import { useChatStore } from "@/stores/chat"

function makeInstance() {
  return reactive({
    creatures: [
      { name: "engine", status: "running", send_channels: ["research"], listen_channels: [] },
      { name: "scout", status: "running", send_channels: [], listen_channels: ["research"] },
      {
        name: "both",
        status: "idle",
        send_channels: ["research", "leads"],
        listen_channels: ["research", "leads"],
      },
      { name: "loner", status: "running", send_channels: ["leads"], listen_channels: [] },
    ],
    channels: [
      { name: "research", type: "broadcast" },
      { name: "leads", type: "broadcast" },
      { name: "empty", type: "broadcast" },
    ],
  })
}

function groups(wrapper) {
  return wrapper.findAll("[data-group]").map((g) => ({
    key: g.attributes("data-group"),
    header: g.find("div").text().replace(/\s+/g, " "),
    names: g.findAll("[data-testid='creature-row']").map((r) => r.find(".font-medium").text()),
  }))
}

describe("CreaturesPanel channel membership", () => {
  let chat
  let instance

  beforeEach(() => {
    setActivePinia(createPinia())
    chat = useChatStore()
    instance = makeInstance()
  })

  function mountPanel() {
    return mount(CreaturesPanel, {
      props: { instance },
      global: { stubs: { GraphCounts: true, StatusDot: true } },
    })
  }

  it("shows a single flat list on a creature tab", () => {
    chat.activeTab = "engine"
    const wrapper = mountPanel()
    const g = groups(wrapper)
    expect(g).toHaveLength(1)
    expect(g[0].key).toBe("all")
    expect(g[0].names).toEqual(["engine", "scout", "both", "loner"])
  })

  it("splits send-only, listen-only and both into members with counts", () => {
    chat.activeTab = "ch:research"
    const g = groups(mountPanel())
    expect(g.map((x) => x.key)).toEqual(["inside", "outside"])
    expect(g[0].names).toEqual(["engine", "scout", "both"])
    expect(g[0].header).toContain("(3)")
    expect(g[1].names).toEqual(["loner"])
    expect(g[1].header).toContain("(1)")
  })

  it("regroups on channel switch and restores the flat list on a creature tab", async () => {
    chat.activeTab = "ch:research"
    const wrapper = mountPanel()
    chat.activeTab = "ch:leads"
    await nextTick()
    let g = groups(wrapper)
    expect(g[0].names).toEqual(["both", "loner"])
    expect(g[1].names).toEqual(["engine", "scout"])

    chat.activeTab = "scout"
    await nextTick()
    g = groups(wrapper)
    expect(g.map((x) => x.key)).toEqual(["all"])
    expect(g[0].names).toHaveLength(4)
  })

  it("reacts to live membership changes", async () => {
    chat.activeTab = "ch:research"
    const wrapper = mountPanel()
    instance.creatures[3].listen_channels.push("research")
    instance.creatures[0].send_channels.splice(0, 1)
    await nextTick()
    const g = groups(wrapper)
    expect(g[0].names).toEqual(["scout", "both", "loner"])
    expect(g[1].names).toEqual(["engine"])
  })

  it("keeps both groups visible when one is empty", () => {
    chat.activeTab = "ch:empty"
    const g = groups(mountPanel())
    expect(g[0].names).toEqual([])
    expect(g[0].header).toContain("(0)")
    expect(g[1].names).toHaveLength(4)
  })

  it("does not split for a channel tab that is not in this instance", () => {
    chat.activeTab = "ch:elsewhere"
    const g = groups(mountPanel())
    expect(g.map((x) => x.key)).toEqual(["all"])
  })

  it("tolerates creatures without channel arrays", () => {
    instance.creatures.push({ name: "bare", status: "running" })
    chat.activeTab = "ch:research"
    const g = groups(mountPanel())
    expect(g[1].names).toContain("bare")
  })

  it("opens a creature tab when a grouped row is clicked", async () => {
    chat.activeTab = "ch:research"
    const wrapper = mountPanel()
    await wrapper.findAll("[data-testid='creature-row']")[0].trigger("click")
    expect(chat.activeTab).toBe("engine")
  })
})
