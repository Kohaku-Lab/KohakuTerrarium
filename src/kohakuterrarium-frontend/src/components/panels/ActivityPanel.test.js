/**
 * ActivityPanel: the stop button on a running job cancels it on the creature
 * that owns the job, whatever tab is active.
 */

import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import ActivityPanel from "./ActivityPanel.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const peers = {
  id: "graph_peers",
  graph_id: "graph_peers",
  type: "terrarium",
  has_root: false,
  creatures: [{ name: "ch-0576" }, { name: "ch-1234" }],
  channels: [{ name: "ch-0576" }],
}

describe("ActivityPanel stop job", () => {
  let chat
  let stop

  beforeEach(() => {
    setActivePinia(createPinia())
    chat = useChatStore(peers.id)
    stop = vi.spyOn(terrariumAPI, "stopCreatureTask").mockResolvedValue({})
  })
  afterEach(() => vi.restoreAllMocks())

  async function stopFirstJob(instance) {
    const wrapper = mount(ActivityPanel, { props: { instance } })
    await wrapper.find("button[title]").trigger("click")
    await Promise.resolve()
  }

  it("routes to the job's creature, not the active tab or the first creature", async () => {
    chat.tabs = ["ch-0576", "ch:ch-0576"]
    chat.activeTab = "ch:ch-0576"
    chat.runningJobs = { job_1: { name: "bash", tab: "ch-1234", startedAt: Date.now() } }

    await stopFirstJob(peers)

    expect(stop).toHaveBeenCalledWith("graph_peers", "ch-1234", "job_1")
  })

  it("falls back to the active creature tab for a job without an owner", async () => {
    chat.tabs = ["ch-0576", "ch-1234"]
    chat.activeTab = "ch-1234"
    chat.runningJobs = { job_2: { name: "bash", startedAt: Date.now() } }

    await stopFirstJob(peers)

    expect(stop).toHaveBeenCalledWith("graph_peers", "ch-1234", "job_2")
  })

  it("never targets a root alias the terrarium does not have", async () => {
    chat.tabs = []
    chat.activeTab = null
    chat.runningJobs = { job_3: { name: "bash", startedAt: Date.now() } }

    await stopFirstJob({ ...peers, creatures: [] })

    expect(stop).not.toHaveBeenCalled()
  })

  it("keeps the root alias for a terrarium with a root", async () => {
    chat.tabs = ["root"]
    chat.activeTab = "root"
    chat.runningJobs = { job_4: { name: "bash", tab: "root", startedAt: Date.now() } }

    await stopFirstJob({ ...peers, has_root: true })

    expect(stop).toHaveBeenCalledWith("graph_peers", "root", "job_4")
  })
})
