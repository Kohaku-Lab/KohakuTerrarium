import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  runtimeGraphAPI: { snapshot: vi.fn(() => Promise.resolve({ graphs: [] })) },
  sessionAPI: { removeCreature: vi.fn(), addCreature: vi.fn(), stopActive: vi.fn() },
  terrariumAPI: {
    wireCreature: vi.fn(),
    unwireCreature: vi.fn(),
    mergeGraphs: vi.fn(),
    connect: vi.fn(),
    addChannel: vi.fn(),
    removeChannel: vi.fn(),
    interruptCreature: vi.fn(),
    startCreature: vi.fn(),
    stopCreature: vi.fn(),
    sendToChannel: vi.fn(),
  },
  wiringAPI: { addOutput: vi.fn(), removeOutput: vi.fn() },
}))

import { sessionAPI, terrariumAPI, wiringAPI } from "@/utils/api"
import { useNotificationsStore } from "@/stores/notifications"

import { connectionIntent, useGraphActions } from "./useGraphActions"

const creature = (id, sessionId = "g1") => ({
  id,
  kind: "creature",
  creature: { id, name: id, sessionId },
})
const channel = (name, sessionId = "g1") => ({
  id: `ch:${sessionId}:${name}`,
  kind: "channel",
  channel: { id: `ch:${sessionId}:${name}`, name, sessionId },
})

function makeView(extra = {}) {
  return { isSample: false, model: { creatures: [{ id: "b", sessionId: "g1" }] }, ...extra }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("connectionIntent", () => {
  it("maps each port and target pair to one intent", () => {
    expect(connectionIntent(creature("a"), "send", channel("x"))).toBe("send")
    expect(connectionIntent(channel("x"), "out", creature("a"))).toBe("listen")
    expect(connectionIntent(creature("a"), "wire", creature("b"))).toBe("wire")
    expect(connectionIntent(creature("a"), "send", creature("b"))).toBe("bridge")
  })

  it("rejects drops that have no meaning", () => {
    expect(connectionIntent(creature("a"), "wire", channel("x"))).toBeNull()
    expect(connectionIntent(channel("x"), "out", channel("y"))).toBeNull()
    expect(connectionIntent(creature("a"), "send", creature("a"))).toBeNull()
    expect(connectionIntent(null, "send", channel("x"))).toBeNull()
  })
})

describe("graph actions", () => {
  it("merges sessions before wiring a creature to another session's channel", async () => {
    terrariumAPI.mergeGraphs.mockResolvedValue({ session_id: "g2", merged: true })
    const actions = useGraphActions(makeView())
    await actions.connect("listen", channel("x", "g2"), creature("a", "g1"))
    expect(terrariumAPI.mergeGraphs).toHaveBeenCalledWith("g2", "g1", "x")
    expect(terrariumAPI.wireCreature).toHaveBeenCalledWith("g2", "a", "x", "listen")
  })

  it("wires within one session without merging", async () => {
    const actions = useGraphActions(makeView())
    await actions.connect("send", creature("a"), channel("x"))
    expect(terrariumAPI.mergeGraphs).not.toHaveBeenCalled()
    expect(terrariumAPI.wireCreature).toHaveBeenCalledWith("g1", "a", "x", "send")
  })

  it("creates an output wire by creature id with content forwarded", async () => {
    const actions = useGraphActions(makeView())
    await actions.connect("wire", creature("a"), creature("b"))
    expect(wiringAPI.addOutput).toHaveBeenCalledWith(
      "g1",
      "a",
      expect.objectContaining({ to: "b", with_content: true }),
    )
  })

  it("removes both directions of a send+listen membership", async () => {
    const actions = useGraphActions(makeView())
    await actions.removeEdge({
      kind: "channel",
      mode: "both",
      sessionId: "g1",
      source: "a",
      channelName: "x",
    })
    expect(terrariumAPI.unwireCreature.mock.calls).toEqual([
      ["g1", "a", "x", "send"],
      ["g1", "a", "x", "listen"],
    ])
  })

  it("removes an output wire by its edge id", async () => {
    const actions = useGraphActions(makeView())
    await actions.removeEdge({ kind: "wire", sessionId: "g1", source: "a", edgeId: "w1" })
    expect(wiringAPI.removeOutput).toHaveBeenCalledWith("g1", "a", "w1")
  })

  it("replaces a wire to change its options", async () => {
    const actions = useGraphActions(makeView())
    await actions.updateWire(
      { kind: "wire", sessionId: "g1", source: "a", target: "b", edgeId: "w1" },
      { withContent: false, prompt: "hi" },
    )
    expect(wiringAPI.removeOutput).toHaveBeenCalledWith("g1", "a", "w1")
    expect(wiringAPI.addOutput).toHaveBeenCalledWith(
      "g1",
      "a",
      expect.objectContaining({ to: "b", with_content: false, prompt: "hi" }),
    )
  })

  it("reverses a wire, keeping its payload and prompt", async () => {
    const actions = useGraphActions(makeView())
    await actions.reverseWire({
      kind: "wire",
      sessionId: "g1",
      source: "a",
      target: "b",
      edgeId: "w1",
      withContent: false,
      prompt: "check this",
    })
    expect(wiringAPI.removeOutput).toHaveBeenCalledWith("g1", "a", "w1")
    expect(wiringAPI.addOutput).toHaveBeenCalledWith(
      "g1",
      "b",
      expect.objectContaining({ to: "a", with_content: false, prompt: "check this" }),
    )
  })

  it("wires a freshly added creature from its upstream when asked", async () => {
    sessionAPI.addCreature.mockResolvedValue({ creature_id: "new1" })
    const actions = useGraphActions(makeView())
    await actions.addCreature("g1", { name: "n", configPath: "/c", wireFrom: "a" })
    expect(sessionAPI.addCreature).toHaveBeenCalledWith(
      "g1",
      expect.objectContaining({ name: "n", configPath: "/c" }),
    )
    expect(wiringAPI.addOutput).toHaveBeenCalledWith(
      "g1",
      "a",
      expect.objectContaining({ to: "new1" }),
    )
  })

  it("reports a failed call as an error toast and resolves null", async () => {
    sessionAPI.removeCreature.mockRejectedValue({ response: { data: { detail: "privileged" } } })
    const actions = useGraphActions(makeView())
    const result = await actions.removeCreature({ id: "a", sessionId: "g1" })
    expect(result).toBeNull()
    const toast = useNotificationsStore().toasts.at(-1)
    expect(toast).toMatchObject({ level: "error", body: "privileged" })
  })

  it("never touches the backend while showing sample data", async () => {
    const actions = useGraphActions(makeView({ isSample: true }))
    await actions.removeCreature({ id: "a", sessionId: "g1" })
    await actions.connect("send", creature("a"), channel("x"))
    expect(sessionAPI.removeCreature).not.toHaveBeenCalled()
    expect(terrariumAPI.wireCreature).not.toHaveBeenCalled()
  })
})
