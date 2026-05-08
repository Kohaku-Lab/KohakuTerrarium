<template>
  <div class="p-4 text-xs">
    <div v-if="loading" class="text-warm-400 text-center py-6">Loading triggers...</div>
    <div v-else-if="error" class="text-coral py-2">
      {{ error }}
    </div>
    <div v-else-if="triggers.length === 0" class="text-warm-400 text-center py-6">No active triggers.</div>
    <div v-else class="flex flex-col gap-1">
      <div v-for="t in triggers" :key="t.trigger_id" class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2 flex items-center gap-2">
        <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="t.running ? 'bg-aquamarine kohaku-pulse' : 'bg-warm-400'" />
        <div class="flex-1 min-w-0">
          <div class="font-medium text-warm-700 dark:text-warm-300">
            {{ t.trigger_type }}
          </div>
          <div class="text-[10px] font-mono text-warm-400 truncate">
            {{ t.trigger_id }}
          </div>
        </div>
        <div class="text-[10px] text-warm-500 font-mono shrink-0">
          {{ formatTs(t.created_at) }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const triggers = ref([])
const loading = ref(false)
const error = ref("")
const target = computed(() => {
  const creatures = props.instance?.creatures || []
  if (creatures.length === 0) return null
  if (creatures.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures[0].name
})

async function load() {
  const sid = props.instance?.graph_id || props.instance?.id
  if (!sid) {
    triggers.value = []
    return
  }
  if (!target.value) {
    error.value = "Triggers are only available for root/creature tabs."
    triggers.value = []
    return
  }
  loading.value = true
  error.value = ""
  try {
    const data = await terrariumAPI.listTriggers(sid, target.value)
    triggers.value = Array.isArray(data) ? data : []
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
    triggers.value = []
  } finally {
    loading.value = false
  }
}

function formatTs(ts) {
  if (!ts) return "—"
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return ts
  }
}

onMounted(load)
watch(() => [props.instance?.id, target.value], load)
</script>
