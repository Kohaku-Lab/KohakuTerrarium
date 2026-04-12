<template>
  <div class="p-4 text-xs">
    <div v-if="loading" class="text-warm-400 text-center py-6">Loading extensions...</div>
    <div v-else-if="error" class="text-coral py-2">
      {{ error }}
    </div>
    <div v-else-if="items.length === 0" class="text-warm-400 text-center py-6">
      No packages installed.
    </div>
    <div v-else class="flex flex-col gap-1">
      <div
        v-for="p in items"
        :key="p.name + ':' + (p.version || '')"
        class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2"
      >
        <div class="flex items-center gap-2">
          <div :class="typeIcon(p.type)" class="text-[13px] text-warm-500" />
          <span class="font-medium text-warm-700 dark:text-warm-300">{{ p.name }}</span>
          <span class="flex-1" />
          <span class="text-[10px] font-mono text-warm-400">
            {{ p.version || "local" }}
          </span>
        </div>
        <div v-if="p.origin || p.path" class="text-[10px] text-warm-500 font-mono mt-0.5 truncate">
          {{ p.origin || p.path }}
        </div>
        <div v-if="p.description" class="text-[10px] text-warm-500 mt-0.5">
          {{ p.description }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue"

import { registryAPI } from "@/utils/api"

const items = ref([])
const loading = ref(false)
const error = ref("")

function typeIcon(t) {
  return (
    {
      creature: "i-carbon-bot",
      terrarium: "i-carbon-network-4",
      tool: "i-carbon-tools",
      plugin: "i-carbon-plug",
    }[t] || "i-carbon-cube"
  )
}

async function load() {
  loading.value = true
  error.value = ""
  try {
    const data = await registryAPI.listLocal()
    // The endpoint may return either a flat list or a grouped dict;
    // normalize to a flat array with a `type` key.
    if (Array.isArray(data)) {
      items.value = data
    } else if (data && typeof data === "object") {
      const out = []
      for (const [type, arr] of Object.entries(data)) {
        if (Array.isArray(arr)) {
          for (const it of arr) out.push({ ...it, type })
        }
      }
      items.value = out
    } else {
      items.value = []
    }
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
