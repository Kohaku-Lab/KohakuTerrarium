/**
 * Deterministic sample snapshots in the `GET /api/runtime/graph` shape, for
 * judging the graph view at scale without running a large team.
 */

import { HOST_SITE } from "@/utils/graph/data/model"

/**
 * `privileged`: privileged nodes per session. Like real ones they send and
 * listen on every channel; the first channel is fed by them alone (an inlet).
 */
export const SAMPLE_PRESETS = {
  small: { creatures: 6, channels: 2, hosts: 1, sessions: 1, privileged: 1 },
  team: { creatures: 16, channels: 5, hosts: 2, sessions: 1, privileged: 3 },
  large: { creatures: 48, channels: 14, hosts: 4, sessions: 1, privileged: 3 },
  fleet: { creatures: 36, channels: 10, hosts: 3, sessions: 4, privileged: 2 },
}

const PRIVILEGED_NAMES = ["lead", "ops", "monitor", "steward"]

const ROLES = [
  "planner",
  "coder",
  "reviewer",
  "tester",
  "researcher",
  "writer",
  "critic",
  "scout",
  "analyst",
  "builder",
  "auditor",
  "designer",
]
const TOPICS = ["tasks", "reviews", "findings", "status", "drafts", "alerts", "specs", "results"]
const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "gpt-5.5", "claude-haiku-4-5"]
const STATUS_ROLL = [
  ["busy", 0.25],
  ["idle", 0.55],
  ["paused", 0.07],
  ["stopped", 0.13],
]

function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function pickStatus(rand) {
  let roll = rand()
  for (const [status, weight] of STATUS_ROLL) {
    if (roll < weight) return status
    roll -= weight
  }
  return "idle"
}

function pickDistinct(rand, pool, count) {
  const copy = [...pool]
  const out = []
  while (copy.length && out.length < count)
    out.push(copy.splice(Math.floor(rand() * copy.length), 1)[0])
  return out
}

function buildSession(rand, index, spec, hostIds) {
  const graphId = `graph_sample${index}`
  const lead = Math.min(Math.max(1, spec.privileged || 1), spec.creatures)
  const creatures = []
  for (let i = 0; i < spec.creatures; i++) {
    const privileged = i < lead
    const w = i - lead
    const role = privileged
      ? PRIVILEGED_NAMES[i % PRIVILEGED_NAMES.length]
      : ROLES[w % ROLES.length]
    const suffix = !privileged && w >= ROLES.length ? `-${Math.floor(w / ROLES.length) + 1}` : ""
    const status = i === 0 ? "busy" : pickStatus(rand)
    const parent =
      i === 0 ? null : rand() < 0.55 ? creatures[Math.floor(rand() * Math.min(i, 4))] : null
    creatures.push({
      creature_id: `${graphId}_c${i}`,
      name: `${role}${suffix}`,
      graph_id: graphId,
      model: MODELS[Math.floor(rand() * MODELS.length)],
      config_name: role,
      running: status !== "stopped",
      paused: status === "paused",
      killed: false,
      is_processing: status === "busy",
      tools: new Array(4 + Math.floor(rand() * 10)).fill("t"),
      subagents: rand() < 0.4 ? ["explore", "critic"].slice(0, 1 + Math.floor(rand() * 2)) : [],
      listen_channels: [],
      send_channels: [],
      is_privileged: privileged,
      is_root: i === 0,
      parent_creature_id: parent ? parent.creature_id : null,
      recipe_root_id: privileged ? null : `${graphId}_c0`,
      home_node: privileged ? HOST_SITE : hostIds[Math.floor(rand() * hostIds.length)],
      max_context: 200000,
    })
  }
  const privilegedNodes = creatures.slice(0, lead)
  const workers = creatures.length > lead ? creatures.slice(lead) : creatures
  const channels = []
  for (let k = 0; k < spec.channels; k++) {
    const name =
      TOPICS[k % TOPICS.length] +
      (k >= TOPICS.length ? `-${Math.floor(k / TOPICS.length) + 1}` : "")
    channels.push({
      name,
      type: "broadcast",
      description: "",
      message_count: Math.floor(rand() * 80),
    })
    const senders = pickDistinct(rand, workers, 1 + Math.floor(rand() * 3))
    if (k > 0 || workers === creatures) for (const c of senders) c.send_channels.push(name)
    for (const c of pickDistinct(rand, workers, 2 + Math.floor(rand() * 4))) {
      if (!c.listen_channels.includes(name)) c.listen_channels.push(name)
    }
    for (const o of privilegedNodes) {
      if (!o.send_channels.includes(name)) o.send_channels.push(name)
      if (!o.listen_channels.includes(name)) o.listen_channels.push(name)
    }
  }
  const outputEdges = []
  for (let i = 1; i < creatures.length; i++) {
    if (rand() > 0.35) continue
    const from = creatures[i]
    const to = creatures[Math.floor(rand() * creatures.length)]
    if (to === from) continue
    outputEdges.push({
      edge_id: `wire_${from.creature_id}_${to.creature_id}`,
      from: from.creature_id,
      from_name: from.name,
      to: to.name,
      to_creature_id: to.creature_id,
      with_content: rand() > 0.2,
      prompt: rand() > 0.6 ? "Summarise and hand over" : "",
      graph_id: graphId,
    })
  }
  const members = [...new Set(creatures.map((c) => c.home_node))]
  return {
    graph_id: graphId,
    kind: creatures.length > 1 ? "terrarium" : "creature",
    name: `sample-${index + 1}`,
    node_id: HOST_SITE,
    has_root: true,
    is_cluster: members.length > 1,
    members: members.map((node) => ({ node_id: node, graph_id: `${graphId}_${node}` })),
    creatures,
    channels,
    output_edges: outputEdges,
  }
}

/** Build a sample snapshot; `preset` is a SAMPLE_PRESETS key or a spec object. */
export function generateSampleSnapshot(preset = "team", seed = 7) {
  const spec = typeof preset === "string" ? SAMPLE_PRESETS[preset] || SAMPLE_PRESETS.team : preset
  const rand = mulberry32(seed)
  const hostIds = [
    HOST_SITE,
    ...Array.from({ length: Math.max(0, spec.hosts - 1) }, (_, i) => `worker-${i + 1}`),
  ]
  const perSession = Math.max(1, Math.round(spec.creatures / spec.sessions))
  const graphs = []
  for (let s = 0; s < spec.sessions; s++) {
    graphs.push(
      buildSession(
        rand,
        s,
        {
          creatures: perSession,
          channels: Math.max(1, Math.round(spec.channels / spec.sessions)),
          privileged: spec.privileged,
        },
        hostIds,
      ),
    )
  }
  return { version: 1, graphs }
}
