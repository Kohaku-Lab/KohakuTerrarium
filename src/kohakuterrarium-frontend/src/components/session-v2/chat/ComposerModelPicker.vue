<template>
  <div class="kt-v2-composer-model min-w-0" :title="channel ? t('composer.modelChannel') : ''" data-test="v2-composer-model">
    <ModelSwitcherShared />
  </div>
</template>

<script setup>
import { computed, watch } from "vue"

import ModelSwitcherShared from "@/components/chrome/ModelSwitcherShared.vue"
import { provideModelSwitcherContext } from "@/components/chrome/modelSwitcherContext"
import { creatureNameFor, tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useModelInventory } from "@/composables/useModelInventory"
import { useHostsStore } from "@/stores/hosts"
import { terrariumAPI } from "@/utils/api"
import { onLayoutEvent, LAYOUT_EVENTS } from "@/utils/layoutEvents"

/**
 * The composer's model pill: the shared model picker bound to the creature
 * whose conversation is open. A channel conversation has no single model,
 * so the pill is disabled there.
 */
const t = useV2T()
const session = useSessionV2()
const chat = session.chat
const hosts = useHostsStore()

const tab = computed(() => chat.activeTab || "")
const channel = computed(() => tab.value.startsWith("ch:"))
const creatureName = computed(() => (tab.value && !channel.value ? creatureNameFor(tab.value, chat._rootSourceName) : ""))
const creature = computed(() => (session.instance.value?.creatures || []).find((c) => c.name === creatureName.value) || null)

const currentModel = computed(() => {
  const live = chat.modelByTab?.[tab.value]
  return live?.llmName || live?.model || creature.value?.llm_name || creature.value?.model || ""
})

async function switchModel({ selector }) {
  const sid = session.sessionId.value
  const name = creatureName.value
  if (!sid || !creature.value) throw Error("Open a creature's chat first")
  const res = await terrariumAPI.switchCreatureModel(sid, name, selector)
  const canonical = res?.model || selector
  for (const key of new Set([name, tabKeyFor(name, chat._rootSourceName)])) {
    chat.modelByTab[key] = { ...(chat.modelByTab[key] || {}), model: canonical, llmName: canonical }
  }
  await session.refresh()
  return canonical
}

provideModelSwitcherContext({
  instance: session.instance,
  isTerrarium: computed(() => false),
  targetOptions: computed(() => []),
  selectedTarget: computed(() => (creature.value ? creatureName.value : null)),
  currentModel,
  selectTarget: () => {},
  switchModel,
  inventory: useModelInventory(),
  onHostChange: (callback) => watch(() => hosts.activeHostId, callback),
  onOpenRequest: (callback) => onLayoutEvent(LAYOUT_EVENTS.MODEL_CONFIG_OPEN, callback),
})
</script>

<style scoped>
.kt-v2-composer-model :deep(.model-pill) {
  min-width: 0;
  max-width: 20rem;
  min-height: 1.75rem;
  padding: 0.1rem 0.5rem;
  gap: 0.35rem;
  border-color: transparent;
  border-radius: 0.5rem;
  color: var(--kc-idle);
}
.kt-v2-composer-model :deep(.model-pill:hover:not(.is-disabled)) {
  color: var(--kc-text);
}
@container (max-width: 34rem) {
  .kt-v2-composer-model :deep(.model-pill-variation) {
    display: none;
  }
}
</style>
