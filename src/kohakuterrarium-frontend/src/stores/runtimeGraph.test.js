import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

class FakeWebSocket {}

vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: vi.fn(() => ({
    start: vi.fn(),
    stop: vi.fn(),
    isRunning: vi.fn(() => true),
  })),
}))

vi.mock("@/utils/uiPrefs", () => ({
  getHybridPrefSync: vi.fn(() => ({ nodes: {}, connections: {}, groups: {}, view: {} })),
  setHybridPref: vi.fn(),
}))

vi.mock("@/utils/wsUrl", () => ({
  wsUrl: vi.fn((path) => `ws://test${path}`),
}))

vi.mock("@/utils/api", () => ({
  runtimeGraphAPI: { snapshot: vi.fn() },
  terrariumAPI: {
    connect: vi.fn(),
    wireCreature: vi.fn(),
    unwireCreature: vi.fn(),
    stop: vi.fn(),
    addChannel: vi.fn(),
    list: vi.fn(() => Promise.resolve([])),
  },
  agentAPI: {
    list: vi.fn(() => Promise.resolve([])),
  },
  wiringAPI: {
    addOutput: vi.fn(),
    removeOutput: vi.fn(),
  },
}))

import { runtimeGraphAPI, terrariumAPI, wiringAPI } from "@/utils/api"
import { useRuntimeGraphStore } from "./runtimeGraph"

const snapshot = {
  version: 1,
  graphs: [
    {
      graph_id: "graph_1",
      name: "team",
      creatures: [
        {
          creature_id: "alice",
          name: "Alice",
          running: true,
          is_processing: false,
          listen_channels: [],
          send_channels: ["tasks"],
        },
        {
          creature_id: "bob",
          name: "Bob",
          running: true,
          is_processing: true,
          listen_channels: ["tasks"],
          send_channels: [],
        },
      ],
      channels: [
        {
          name: "tasks",
          type: "queue",
          description: "Task queue",
          qsize: 1,
          message_count: 1,
          last_message: { content_preview: "hello" },
        },
      ],
      output_edges: [
        {
          id: "wire_bob_content_simple_noself_811c9dc5",
          edge_id: "wire_bob_content_simple_noself_811c9dc5",
          from: "alice",
          to: "bob",
          to_creature_id: "bob",
          with_content: true,
        },
      ],
    },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  globalThis.WebSocket = FakeWebSocket
  runtimeGraphAPI.snapshot.mockResolvedValue(snapshot)
})

describe("runtime graph store", () => {
  it("normalizes runtime snapshot into graph editor data", async () => {
    const store = useRuntimeGraphStore()

    await store.loadSnapshot()

    expect(store.state.groups).toHaveLength(1)
    expect(store.state.groups[0].id).toBe("graph_1")
    expect(store.nodeById.alice.status).toBe("waiting")
    expect(store.nodeById.bob.status).toBe("running")
    expect(store.nodeById["channel:graph_1:tasks"].kind).toBe("channel")
    expect(store.state.connections.map((c) => c.label).sort()).toEqual(["tasks", "tasks", "wire"])
  })

  it("wires creature-to-creature drags as direct output wiring (no channel)", async () => {
    const store = useRuntimeGraphStore()
    wiringAPI.addOutput.mockResolvedValue({ status: "wired", edge_id: "ed_1" })
    await store.loadSnapshot()

    await store.connect("alice", "bob")

    expect(wiringAPI.addOutput).toHaveBeenCalledWith(
      "graph_1",
      "alice",
      expect.objectContaining({ to: "bob", with_content: true }),
    )
    expect(terrariumAPI.connect).not.toHaveBeenCalled()
    // No await loadSnapshot inside the mutation — the WS pushes the
    // topology_changed event and that triggers the reload.
    expect(runtimeGraphAPI.snapshot).toHaveBeenCalledTimes(1)
  })

  it("toggles channel-edge direction through wire and unwire APIs", async () => {
    const store = useRuntimeGraphStore()
    terrariumAPI.unwireCreature.mockResolvedValue({ status: "unwired" })
    await store.loadSnapshot()
    const edge = store.state.connections.find(
      (c) => c.backend?.kind === "channel_edge" && c.backend.creatureId === "alice",
    )

    await store.toggleDirection(edge.id, "aToB")

    expect(terrariumAPI.unwireCreature).toHaveBeenCalledWith("graph_1", "alice", "tasks", "send")
  })

  it("removes output wiring edges through wiring API", async () => {
    const store = useRuntimeGraphStore()
    wiringAPI.removeOutput.mockResolvedValue({ status: "unwired" })
    await store.loadSnapshot()
    const edge = store.state.connections.find((c) => c.backend?.kind === "output_edge")

    await store.deleteConnection(edge.id)

    expect(wiringAPI.removeOutput).toHaveBeenCalledWith(
      "graph_1",
      "alice",
      "wire_bob_content_simple_noself_811c9dc5",
    )
  })

  it("renders a solo-creature graph as a free card with no membrane", async () => {
    runtimeGraphAPI.snapshot.mockResolvedValue({
      version: 2,
      graphs: [
        {
          graph_id: "graph_solo",
          name: "alice (standalone)",
          creatures: [
            {
              creature_id: "alice",
              name: "Alice",
              running: true,
              is_processing: false,
              listen_channels: [],
              send_channels: [],
            },
          ],
          channels: [],
          output_edges: [],
        },
      ],
    })
    const store = useRuntimeGraphStore()

    await store.loadSnapshot()

    // No membrane for a single creature with no channels — the
    // creature shows up free instead of wrapped in a one-card group.
    expect(store.state.groups).toHaveLength(0)
    const alice = store.nodeById.alice
    expect(alice.groupId).toBeNull()
    // The backend graph id is still tracked separately so wiring
    // calls have the right session id.
    expect(alice.graphId).toBe("graph_solo")
  })

  it("patches channel message websocket events without refetch", async () => {
    const store = useRuntimeGraphStore()
    await store.loadSnapshot()

    store.applyRuntimeEvent({
      type: "channel_message",
      graph_id: "graph_1",
      channel: "tasks",
      sender: "alice",
      content_preview: "latest task",
    })

    const edges = store.state.connections.filter((c) => c.backend?.kind === "channel_edge")
    expect(edges.every((edge) => edge.brief === "latest task")).toBe(true)
  })
})
