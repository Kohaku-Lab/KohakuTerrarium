<template>
  <div class="creature-model-picker min-w-0">
    <ModelSwitcherShared />
  </div>
</template>

<script setup>
import { computed, onScopeDispose, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import ModelSwitcherShared from "@/components/chrome/ModelSwitcherShared.vue"
import { provideModelSwitcherContext } from "@/components/chrome/modelSwitcherContext"
import { useModelInventory } from "@/composables/useModelInventory"
import { useGraphLiveStore } from "@/stores/graph/live"
import { getRuntimeScope } from "@/stores/runtimeScope"
import { terrariumAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  creature: { type: Object, required: true },
  view: { type: Object, required: true },
})

const live = useGraphLiveStore()
const { t } = useI18n()
const runtimeInvalidated = ref(false)
let ownerVersion = 0
let disposed = false

watch(
  getRuntimeScope,
  () => {
    runtimeInvalidated.value = true
    ownerVersion++
  },
  { flush: "sync" },
)
onScopeDispose(() => {
  disposed = true
})

const selectedTarget = computed(() => {
  const { id, sessionId } = props.creature
  const selection = props.view.selection
  if (runtimeInvalidated.value || props.view.isSample || !id || !sessionId) return null
  if (selection?.kind !== "creature" || selection.id !== id) return null
  return props.view.model.creatures.some((c) => c.id === id && c.sessionId === sessionId) ? id : null
})
watch(selectedTarget, () => ownerVersion++, { flush: "sync" })

const instance = computed(() => (selectedTarget.value ? { id: props.creature.sessionId } : null))
const currentModel = computed(() => props.creature.model || "")

async function switchModel({ target, selector }) {
  const id = selectedTarget.value
  const sid = props.creature.sessionId
  if (!id || target !== id) throw Error("Select a live creature first")
  const version = ownerVersion
  const ownsSwitch = () => !disposed && version === ownerVersion && selectedTarget.value === id && props.creature.sessionId === sid
  const requireOwner = () => {
    if (!ownsSwitch()) throw Error("Selected creature or runtime changed")
  }
  requireOwner()
  const res = await terrariumAPI.switchCreatureModel(sid, id, selector)
  requireOwner()
  const canonical = res?.model || selector
  const readbackModel = () => live.model.creatures.find((c) => c.id === id && c.sessionId === sid)?.model
  try {
    await live.refresh()
    if (ownsSwitch() && !live.error && readbackModel() !== canonical) await live.refresh()
    requireOwner()
    if (live.error) throw Error(live.error)
    if (readbackModel() !== canonical) throw Error(`Graph has not confirmed ${canonical}`)
  } catch (err) {
    if (ownsSwitch()) ElMessage.warning(t("modelSwitcher.metadataUnconfirmed", { message: err?.message || String(err) }))
  }
  requireOwner()
  return canonical
}

provideModelSwitcherContext({
  instance,
  isTerrarium: computed(() => false),
  targetOptions: computed(() => []),
  selectedTarget,
  currentModel,
  selectTarget: () => {},
  switchModel,
  inventory: useModelInventory(),
  onHostChange: (callback) => watch([getRuntimeScope, selectedTarget], () => callback(), { flush: "sync" }),
  onOpenRequest: () => () => {},
})
</script>

<style scoped>
.creature-model-picker :deep(.model-pill) {
  min-width: 0;
  max-width: 100%;
}
</style>
