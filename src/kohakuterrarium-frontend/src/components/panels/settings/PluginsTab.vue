<template>
  <div class="p-4 text-xs">
    <div v-if="loading" class="text-warm-400 text-center py-6">Loading plugins...</div>
    <div v-else-if="error" class="text-coral py-2">
      {{ error }}
    </div>
    <div v-else-if="plugins.length === 0" class="text-warm-400 text-center py-6">No plugins loaded.</div>
    <div v-else class="flex flex-col gap-1.5">
      <div v-for="p in plugins" :key="p.name" class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2">
        <div class="flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="p.enabled ? 'bg-aquamarine' : 'bg-warm-400'" />
          <span class="font-medium text-warm-700 dark:text-warm-300">{{ p.name }}</span>
          <span class="flex-1" />
          <span class="text-[10px] font-mono" :class="p.enabled ? 'text-aquamarine' : 'text-warm-400'">{{ p.enabled ? "enabled" : "disabled" }}</span>
        </div>
        <div v-if="p.description" class="text-[10px] text-warm-500 mt-0.5">
          {{ p.description }}
        </div>
        <div v-if="p.error" class="text-[10px] text-coral mt-0.5 font-mono">
          {{ p.error }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { agentAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const plugins = ref([])
const loading = ref(false)
const error = ref("")
const terrariumTarget = computed(() => (props.instance?.type === "terrarium" ? chat.terrariumTarget : null))

async function load() {
  const id = props.instance?.id
  if (!id) return
  if (props.instance?.type === "terrarium" && !terrariumTarget.value) {
    error.value = "Plugins are only available for root/creature tabs."
    plugins.value = []
    return
  }
  loading.value = true
  error.value = ""
  try {
    const data = props.instance?.type === "terrarium" ? await terrariumAPI.listPlugins(id, terrariumTarget.value) : await agentAPI.listPlugins(id)
    plugins.value = Array.isArray(data) ? data : []
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
    plugins.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => [props.instance?.id, terrariumTarget.value], load)
</script>
