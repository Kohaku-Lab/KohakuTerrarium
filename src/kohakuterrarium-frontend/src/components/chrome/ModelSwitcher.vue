<template>
  <ModelSwitcherShared />
</template>

<script setup>
import { computed, watch } from "vue"
import { useRoute } from "vue-router"

import { useInstanceContext } from "@/components/chrome/instanceContext"
import { useModelInventory } from "@/composables/useModelInventory"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { terrariumAPI } from "@/utils/api"
import { onLayoutEvent, LAYOUT_EVENTS } from "@/utils/layoutEvents"

import ModelSwitcherShared from "./ModelSwitcherShared.vue"
import { provideModelSwitcherContext } from "./modelSwitcherContext"

// Dashboard binding of the shared model picker. This is the only place that
// reaches the Dashboard's router/instance/hosts/chat stores; it hands the one
// shared leaf a narrow, host-neutral context (selected instance, target/model
// reads, switch + target dispatch, host-keyed inventory, subscriptions).
const props = defineProps({
  instanceId: { type: String, default: "" },
})

const route = useRoute()
const chat = useChatStore()
const hosts = useHostsStore()
const instances = useInstancesStore()
const inventory = useModelInventory()

const { instance } = useInstanceContext(props, route, instances)
const isTerrarium = computed(() => instance.value?.type === "terrarium")
const targetOptions = computed(() => {
  const inst = instance.value
  if (inst?.type !== "terrarium") return []
  return [...(inst.has_root ? [{ value: "root", label: "root" }] : []), ...(inst.creatures || []).map((c) => ({ value: c.name, label: c.name }))]
})
const terrariumTarget = computed(() => (isTerrarium.value ? chat.terrariumTarget : null))
const selectedTarget = computed(() => terrariumTarget.value || targetOptions.value[0]?.value || null)

// Resolve a target key to its creature record. ``root`` is a tab
// alias for the privileged creature in recipe terrariums.
function creatureForTarget(inst, target) {
  if (!inst || !target) return null
  if (target === "root") return inst.creatures?.find((c) => c.is_root) || null
  return inst.creatures?.find((c) => c.name === target) || null
}

const currentModel = computed(() => {
  const inst = instance.value
  // ``llm_name`` carries the canonical ``provider/name[@variations]`` —
  // prefer it over ``model`` (raw API id) so the pill and picker-draft
  // survive a page refresh with the full identifier intact.
  if (inst?.type === "terrarium") {
    const target = selectedTarget.value
    if (!target) return ""
    // The pill must ALWAYS track the SELECTED creature: live per-tab
    // info (WS session_info, keyed by source name) first, then the
    // instance snapshot. The global chat.sessionInfo is deliberately
    // NOT consulted here — it tracks the primary creature only, and
    // preferring it made every target show the primary's model.
    const creature = creatureForTarget(inst, target)
    const live = chat.modelByTab[target] || (creature?.name && chat.modelByTab[creature.name]) || null
    return live?.llmName || live?.model || creature?.llm_name || creature?.model || (target === "root" ? inst.llm_name || inst.model || "" : "")
  }
  const soloTab = inst?.creatures?.[0]?.name || chat.terrariumTarget
  const live = (soloTab && chat.modelByTab[soloTab]) || null
  return live?.llmName || live?.model || chat.sessionInfo.llmName || chat.sessionInfo.model || inst?.llm_name || inst?.model || ""
})

function selectTarget(target) {
  if (chat.tabs.includes(target)) chat.setActiveTab(target)
  else chat.openTab(target)
}

async function switchModel({ target, selector }) {
  const inst = instance.value
  const id = inst?.id
  const sid = inst?.graph_id || id
  const resolvedTarget = target || inst?.creatures?.[0]?.name
  if (!resolvedTarget) throw Error("Select a creature first")
  // Unified routing — every session uses
  // ``/sessions/{sid}/creatures/{name}/model``. Solo sessions
  // resolve their lone creature automatically; multi-creature
  // sessions follow the user's active tab.
  const res = await terrariumAPI.switchCreatureModel(sid, resolvedTarget, selector)
  // The backend returns the canonical ``provider/name[@variations]``
  // identifier — use it so the pill matches what /model would show.
  const canonical = res?.model || selector
  await instances.fetchOne(id)
  // Update the per-creature entry immediately; the creature's own
  // ``session_info`` event confirms (and adds max_context) later.
  const creature = creatureForTarget(inst, resolvedTarget)
  const entry = { ...(chat.modelByTab[resolvedTarget] || {}), model: canonical, llmName: canonical }
  chat.modelByTab[resolvedTarget] = entry
  if (creature?.name && creature.name !== resolvedTarget) {
    chat.modelByTab[creature.name] = { ...entry }
  }
  if (creature?.is_root && inst?.has_root) {
    chat.modelByTab["root"] = { ...entry }
  }
  // Keep the session-level fallback in sync only when the primary
  // creature (or a solo session's lone creature) was the target.
  const isPrimary = (inst?.creatures?.length || 0) <= 1 || chat.tabs[0] === resolvedTarget || (chat.tabs[0] === "root" && (resolvedTarget === "root" || creature?.is_root))
  if (isPrimary) {
    chat.sessionInfo.llmName = canonical
    chat.sessionInfo.model = canonical
  }
  return canonical
}

provideModelSwitcherContext({
  instance,
  isTerrarium,
  targetOptions,
  selectedTarget,
  currentModel,
  selectTarget,
  switchModel,
  inventory,
  onHostChange: (callback) =>
    watch(
      () => hosts.activeHostId,
      () => callback(),
    ),
  onOpenRequest: (callback) => onLayoutEvent(LAYOUT_EVENTS.MODEL_CONFIG_OPEN, callback),
})
</script>
