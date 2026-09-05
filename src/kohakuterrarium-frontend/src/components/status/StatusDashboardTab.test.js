/**
 * StatusDashboardTab: the Creatures tab makes a multi-creature session
 * operable from the default layout (focus, start/stop, model) and the rail
 * icon counts members and glows when the graph grows unseen.
 */

import { mount, flushPromises } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus from "element-plus"

vi.mock("@/utils/api", () => {
  const empty = () => ({})
  return {
    default: {},
    configAPI: { getModels: vi.fn().mockResolvedValue([{ name: "gpt-x", provider: "openai" }]) },
    terrariumAPI: {
      startCreature: vi.fn().mockResolvedValue({ status: "started" }),
      stopCreature: vi.fn().mockResolvedValue({ status: "stopped" }),
      switchCreatureModel: vi.fn().mockResolvedValue({}),
      stopCreatureTask: vi.fn(),
    },
    agentAPI: empty(),
    sessionAPI: { getActive: vi.fn().mockResolvedValue({ session_id: "g1", creatures: [] }) },
    runtimeGraphAPI: empty(),
    moduleAPI: empty(),
    wiringAPI: empty(),
    filesAPI: empty(),
    settingsAPI: empty(),
    registryAPI: empty(),
    packagesAPI: empty(),
    labAPI: empty(),
    extensionsAPI: empty(),
    statsAPI: empty(),
    attachAPI: empty(),
    nodesAPI: empty(),
  }
})

import StatusDashboardTab from "./StatusDashboardTab.vue"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { terrariumAPI } from "@/utils/api"

const INSTANCE = {
  id: "g1",
  graph_id: "g1",
  type: "terrarium",
  status: "running",
  creatures: [
    { creature_id: "c1", name: "alice", status: "running", llm_name: "openai/gpt-x" },
    { creature_id: "c2", name: "bob", status: "idle" },
  ],
  channels: [],
}

function mountTab(instance = INSTANCE) {
  return mount(StatusDashboardTab, {
    props: { instance },
    global: { plugins: [ElementPlus], stubs: { ModulesPanel: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  terrariumAPI.startCreature.mockClear()
  terrariumAPI.stopCreature.mockClear()
  terrariumAPI.switchCreatureModel.mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("StatusDashboardTab — Creatures tab", () => {
  it("does not exist for a solo session, and appears with a glow once a second creature joins", async () => {
    const solo = { ...INSTANCE, creatures: [INSTANCE.creatures[0]] }
    const w = mountTab(solo)
    await flushPromises()
    expect(w.find('[data-testid="status-tab-creatures"]').exists()).toBe(false)
    expect(w.find('[data-testid="status-creature-count"]').exists()).toBe(false)
    await w.setProps({ instance: INSTANCE })
    const rail = w.find('[data-testid="status-tab-creatures"]')
    expect(rail.exists()).toBe(true)
    expect(rail.classes()).toContain("rail-glow")
    expect(w.find('[data-testid="status-creature-count"]').text()).toBe("2")
  })

  it("falls back to the session tab when the graph shrinks to one creature", async () => {
    const w = mountTab()
    await flushPromises()
    await w.find('[data-testid="status-tab-creatures"]').trigger("click")
    expect(w.find('[data-testid="status-creature-alice"]').exists()).toBe(true)
    await w.setProps({ instance: { ...INSTANCE, creatures: [INSTANCE.creatures[0]] } })
    expect(w.find('[data-testid="status-tab-creatures"]').exists()).toBe(false)
    expect(w.find('[data-testid="status-tab-session"]').classes()).toContain("bg-iolite/10")
  })

  it("counts members on the rail icon and lists them with controls", async () => {
    const w = mountTab()
    await flushPromises()
    expect(w.find('[data-testid="status-creature-count"]').text()).toBe("2")
    await w.find('[data-testid="status-tab-creatures"]').trigger("click")
    expect(w.find('[data-testid="status-creature-alice"]').exists()).toBe(true)
    expect(w.find('[data-testid="status-stop-alice"]').exists()).toBe(true)
    expect(w.find('[data-testid="status-start-bob"]').exists()).toBe(true)
  })

  it("starts and stops creatures by name in the session and refreshes the roster", async () => {
    const instances = useInstancesStore()
    const refresh = vi.spyOn(instances, "fetchOne").mockResolvedValue(null)
    const w = mountTab()
    await flushPromises()
    await w.find('[data-testid="status-tab-creatures"]').trigger("click")
    await w.find('[data-testid="status-stop-alice"]').trigger("click")
    await flushPromises()
    expect(terrariumAPI.stopCreature).toHaveBeenCalledWith("g1", "alice")
    await w.find('[data-testid="status-start-bob"]').trigger("click")
    await flushPromises()
    expect(terrariumAPI.startCreature).toHaveBeenCalledWith("g1", "bob")
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it("focuses a creature's chat tab when its name is clicked", async () => {
    const chat = useChatStore()
    const open = vi.spyOn(chat, "openTab").mockImplementation(() => {})
    const w = mountTab()
    await flushPromises()
    await w.find('[data-testid="status-tab-creatures"]').trigger("click")
    await w.find('[data-testid="status-creature-bob"] button').trigger("click")
    expect(open).toHaveBeenCalledWith("bob")
  })

  it("glows when the graph grows while the tab is not in view, until it is opened", async () => {
    const w = mountTab()
    await flushPromises()
    const rail = () => w.find('[data-testid="status-tab-creatures"]')
    expect(rail().classes()).not.toContain("rail-glow")
    await w.setProps({
      instance: {
        ...INSTANCE,
        creatures: [...INSTANCE.creatures, { creature_id: "c3", name: "carol", status: "idle" }],
      },
    })
    expect(rail().classes()).toContain("rail-glow")
    expect(w.find('[data-testid="status-creature-count"]').text()).toBe("3")
    await rail().trigger("click")
    expect(rail().classes()).not.toContain("rail-glow")
  })
})
