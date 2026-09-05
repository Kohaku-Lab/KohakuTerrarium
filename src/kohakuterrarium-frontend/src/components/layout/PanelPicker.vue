<template>
  <el-dialog v-model="open" title="Choose a panel" width="500px" :close-on-click-modal="true" @close="$emit('cancel')">
    <el-input v-model="query" placeholder="Search panels..." clearable size="small" class="mb-3" />

    <div class="flex flex-col gap-1 max-h-96 overflow-y-auto">
      <button v-for="p in filtered" :key="p.id" class="flex items-center gap-2 px-3 py-2 rounded text-left hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" :class="currentPanelId === p.id ? 'bg-iolite/10' : ''" @click="select(p.id)">
        <div class="flex-1 min-w-0">
          <div class="text-xs font-medium text-warm-700 dark:text-warm-300 truncate">
            {{ panelLabel(p.id, p.label) }}
          </div>
          <div v-if="panelDescription(p.id, p.description)" class="text-[10px] text-warm-500 truncate" :data-testid="`panel-desc-${p.id}`">{{ panelDescription(p.id, p.description) }}</div>
          <div class="text-[10px] text-warm-400 font-mono truncate">{{ p.id }} · {{ p.orientation }}</div>
          <div v-if="p.preferredZones?.length" class="text-[10px] text-warm-500 truncate">prefers: {{ p.preferredZones.join(", ") }}</div>
        </div>
        <div v-if="zoneId && p.preferredZones?.length && !p.preferredZones.includes(zoneId)" class="text-amber text-[9px] flex items-center gap-0.5 shrink-0" :title="`Prefers ${p.preferredZones[0]}`">
          <span class="i-carbon-warning-alt" />
        </div>
      </button>
      <div v-if="filtered.length === 0" class="text-warm-400 text-center py-6 text-xs">No panels match "{{ query }}"</div>
    </div>

    <template #footer>
      <el-button size="small" @click="$emit('cancel')">Cancel</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { useRoute } from "vue-router"

import { useLayoutStore } from "@/stores/layout"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  zoneId: { type: String, default: "" },
  currentPanelId: { type: String, default: "" },
})

const emit = defineEmits(["update:modelValue", "select", "cancel"])

const layout = useLayoutStore()
const instances = useInstancesStore()
const { panelLabel, panelDescription } = useI18n()

const open = ref(props.modelValue)
watch(
  () => props.modelValue,
  (v) => {
    open.value = v
  },
)
watch(open, (v) => {
  emit("update:modelValue", v)
})

const query = ref("")

const currentInstance = computed(() => {
  const id = String(useRoute().params.id || "")
  if (!id) return instances.current || null
  if (instances.current?.id === id) return instances.current
  return instances.list.find((item) => item.id === id) || null
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  // Hidden legacy aliases and non-user-facing chrome never appear here.
  const all = layout.visiblePanelList.filter((p) => p.id !== "status-bar")
  if (!q) return all
  return all.filter((p) => {
    const hay = `${p.id} ${p.label || ""} ${panelLabel(p.id, p.label)} ${panelDescription(p.id, p.description)} ${(p.preferredZones || []).join(" ")}`
    return hay.toLowerCase().includes(q)
  })
})

function select(panelId) {
  emit("select", panelId)
  open.value = false
}
</script>
