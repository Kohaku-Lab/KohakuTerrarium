<template>
  <div class="card p-4">
    <h2 class="text-sm font-medium text-warm-700 dark:text-warm-300 mb-3">Health</h2>

    <!-- Providers -->
    <section class="mb-3">
      <h3 class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">Providers</h3>
      <div v-if="providersLoading" class="text-warm-400 italic text-xs">Loading…</div>
      <div v-else-if="providers.length === 0" class="text-warm-400 italic text-xs">No providers configured. Add API keys in Settings.</div>
      <ul v-else class="space-y-1 text-xs">
        <li v-for="p in providers" :key="p.name" class="flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full" :class="p.has_key ? 'bg-iolite' : 'bg-warm-400'" />
          <span class="text-warm-700 dark:text-warm-300">{{ p.name }}</span>
          <span class="text-warm-400 ml-auto">{{ p.has_key ? "ok" : "no key" }}</span>
        </li>
      </ul>
    </section>

    <!-- MCP -->
    <section class="mb-3">
      <h3 class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">MCP</h3>
      <div v-if="mcpLoading" class="text-warm-400 italic text-xs">Loading…</div>
      <div v-else-if="mcp.length === 0" class="text-warm-400 italic text-xs">No MCP servers configured.</div>
      <ul v-else class="space-y-1 text-xs">
        <li v-for="m in mcp" :key="m.name" class="flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full bg-warm-400" />
          <span class="text-warm-700 dark:text-warm-300">{{ m.name }}</span>
          <span class="text-warm-400 ml-auto">{{ m.transport ?? "—" }}</span>
        </li>
      </ul>
    </section>

    <!-- Disk -->
    <section v-if="diskInfo">
      <h3 class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">Disk</h3>
      <div class="text-xs text-warm-700 dark:text-warm-300">Sessions: {{ diskInfo.totalLabel }} across {{ diskInfo.count }} files</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { settingsAPI, sessionAPI } from "@/utils/api"

const providers = ref([])
const providersLoading = ref(true)
const mcp = ref([])
const mcpLoading = ref(true)
const diskInfo = ref(null)

onMounted(async () => {
  // Providers — combine known backends + key presence. The API
  // returns wrappers: `getBackends` → `{backends: [...]}`,
  // `getKeys` → `{providers: [...]}`. Narrow defensively.
  try {
    const backendsResp = await settingsAPI.getBackends()
    const keysResp = await settingsAPI.getKeys()
    const backendList = Array.isArray(backendsResp) ? backendsResp : (backendsResp?.backends ?? [])
    const keysList = Array.isArray(keysResp) ? keysResp : (keysResp?.providers ?? keysResp?.keys ?? [])
    const keySet = new Set(keysList.map((k) => k.provider ?? k.name))
    providers.value = backendList.map((b) => ({
      name: b.name ?? b.provider,
      has_key: keySet.has(b.name ?? b.provider) || b.has_key === true || b.available === true,
    }))
  } catch {
    providers.value = []
  } finally {
    providersLoading.value = false
  }
  // MCP — `listMCP` returns either an array or a wrapper.
  try {
    const resp = await settingsAPI.listMCP()
    const list = Array.isArray(resp) ? resp : (resp?.servers ?? resp?.items ?? [])
    mcp.value = Array.isArray(list) ? list : []
  } catch {
    mcp.value = []
  } finally {
    mcpLoading.value = false
  }
  // Disk — best effort; sessions endpoint returns size info on each entry.
  try {
    const data = await sessionAPI.list({ limit: 200 })
    const list = Array.isArray(data) ? data : (data?.sessions ?? data?.items ?? [])
    const items = Array.isArray(list) ? list : []
    let total = 0
    for (const s of items) total += s.size_bytes ?? s.size ?? 0
    diskInfo.value = {
      count: items.length,
      totalLabel: formatBytes(total),
    }
  } catch {
    diskInfo.value = null
  }
})

function formatBytes(b) {
  if (!b) return "0 B"
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(1)} GB`
}
</script>
