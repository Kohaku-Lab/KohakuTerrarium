/**
 * Spawn-tree view input: who created whom, drawn as a tree under one "User"
 * root.
 *
 * - A spawned creature hangs under its spawner (`parent_creature_id`).
 * - A terrarium's creatures hang under the privileged root created with them
 *   (`recipe_root_id`): user → terrarium root → its creatures.
 * - Only what the user made directly hangs under the User: privileged nodes,
 *   and creatures whose spawner / recipe root is gone (deleted, outside the
 *   scope or the session). A spawn cycle is cut once.
 * - With several sessions in scope, each session is a node under the User.
 *
 * Channels and wires are not drawn: each card lists what it listens to,
 * sends to and wires to. Pure.
 */

export const USER_NODE_ID = "tree:user"
const TIER_PRIVILEGED_SIZE = { width: 312, height: 84 }

export function sessionNodeId(sessionId) {
  return `tree:session:${sessionId}`
}

/** Creator per creature id (null = the User), with cycles broken. */
export function spawnParents(creatures) {
  const byId = new Map(creatures.map((c) => [c.id, c]))
  const present = (id, c) => {
    const p = id && id !== c.id ? byId.get(id) : null
    return p && p.sessionId === c.sessionId ? p.id : null
  }
  const direct = (c) => present(c.parentId, c) || present(c.recipeRootId, c)
  const parent = new Map()
  for (const c of creatures) {
    const path = [c.id]
    let up = direct(c)
    while (up && !path.includes(up)) {
      path.push(up)
      up = direct(byId.get(up))
    }
    // A chain that comes back to c is a cycle; only its smallest id loses its parent.
    const onCycle = up === c.id
    const breaker = onCycle && path.every((id) => c.id <= id)
    parent.set(c.id, breaker ? null : direct(c))
  }
  return parent
}

/** Build {nodes, edges, groups, multiHost} for the spawn-tree view. */
export function buildTierInput(projection) {
  const creatures = projection.nodes.filter((n) => n.kind === "creature").map((n) => n.creature)
  const parent = spawnParents(creatures)
  const sessionIds = [...new Set(creatures.map((c) => c.sessionId))]
  const multiSession = sessionIds.length > 1
  const sessionName = new Map((projection.sessions || []).map((s) => [s.id, s.name]))
  const wiresOut = new Map()
  for (const e of projection.edges) {
    if (e.kind !== "wire") continue
    if (!wiresOut.has(e.source)) wiresOut.set(e.source, [])
    wiresOut.get(e.source).push(e)
  }

  const nodes = []
  const edges = []
  const link = (source, target, sessionId) =>
    edges.push({ id: `spawn:${source}:${target}`, kind: "authority", source, target, sessionId })
  if (creatures.length) nodes.push({ id: USER_NODE_ID, kind: "user", parent: null })
  if (multiSession)
    for (const sid of sessionIds) {
      const id = sessionNodeId(sid)
      nodes.push({
        id,
        kind: "session",
        parent: null,
        sessionId: sid,
        label: sessionName.get(sid) || sid,
      })
      link(USER_NODE_ID, id, sid)
    }
  for (const c of creatures) {
    nodes.push({
      id: c.id,
      kind: "tier",
      parent: null,
      creature: c,
      wiresOut: wiresOut.get(c.id) || [],
      // A privileged card lists no channels (its access is total), only its wires row.
      size: c.privileged ? TIER_PRIVILEGED_SIZE : undefined,
    })
    const top = multiSession ? sessionNodeId(c.sessionId) : USER_NODE_ID
    link(parent.get(c.id) || top, c.id, c.sessionId)
  }
  return { nodes, edges, groups: [], multiHost: projection.stats.hosts > 1 }
}
