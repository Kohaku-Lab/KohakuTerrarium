import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"

export function navigateToAttention({ scope, tab }) {
  if (!scope || !tab) return false

  const tabs = useTabsStore()
  const id = `attach:${scope}`
  tabs.openTab({ kind: "attach", id, target: scope })
  tabs.activateTab(id)
  useChatStore(scope).openTab(tab)
  return true
}

function _matchesInstance(inst, id) {
  if (!inst || !id) return false
  if (inst.id === id || inst.graph_id === id || inst.session_id === id) return true
  return (inst.creatures || []).some(
    (c) => c.creature_id === id || c.agent_id === id || c.name === id,
  )
}

/**
 * Resolve a human-readable attention target for notification bodies.
 *
 * ``scope`` is the attach scope (session/graph id) and ``tab`` the creature
 * source name. Falls back to the raw identifiers when the session is no
 * longer listed (e.g. stopped between the edge and the notification).
 */
export function attentionTargetLabel({ scope, tab }) {
  const instances = useInstancesStore()
  const inst = instances.list.find((it) => _matchesInstance(it, scope)) || null
  if (!inst) return scope || ""
  const sessionName = inst.session_name || inst.config_name || scope
  const creature = (inst.creatures || []).find((c) => c.name === tab || c.creature_id === tab)
  return creature?.name ? `${sessionName} · ${creature.name}` : sessionName
}
