<template>
  <SectionShell :title="t('set.extensions.title')" :hint="t('set.extensions.hint')" :error="error">
    <template #aside>
      <div class="flex items-center gap-3">
        <el-input v-model="query" size="small" clearable class="!w-56" :placeholder="t('set.filter')" />
        <button class="i-carbon-renew text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('set.refresh')" @click="load" />
      </div>
    </template>
    <div v-if="loading" class="text-sm text-warm-400">{{ t("loading") }}</div>
    <div v-else-if="!items.length" class="rounded-lg border border-dashed kt-v2-edge px-4 py-8 text-center text-sm text-warm-400">{{ t("set.extensions.empty") }}</div>
    <template v-else>
      <div v-for="group in groups" :key="group.type" class="flex flex-col gap-2" data-test="v2-settings-extensions">
        <div class="flex items-center gap-2 text-[11px] uppercase tracking-wider text-warm-400"><span :class="typeIcon(group.type)" />{{ group.type }} · {{ group.items.length }}</div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
          <div v-for="p in group.items" :key="`${p.name}:${p.path || ''}`" class="rounded-lg border kt-v2-line px-3 py-2.5">
            <div class="flex items-center gap-2">
              <span class="font-medium text-warm-800 dark:text-warm-100 truncate">{{ p.name }}</span>
              <span class="flex-1" />
              <span v-if="p.source" class="font-mono text-[11px] text-warm-500 shrink-0">{{ p.source }}</span>
            </div>
            <p v-if="p.description" class="mt-1 text-xs text-warm-500 line-clamp-2">{{ p.description }}</p>
            <p v-if="p.path" class="mt-1 font-mono text-[11px] text-warm-400 truncate" :title="p.path">{{ p.path }}</p>
          </div>
        </div>
      </div>
      <div v-if="!groups.length" class="text-sm text-warm-400">{{ t("set.noMatches") }}</div>
    </template>
  </SectionShell>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { errorText } from "@/components/session-v2/model/settings/settingsModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { onShownAgain } from "@/components/session-v2/settings/useSectionTarget"
import { registryAPI } from "@/utils/api"

/** Installed packages (creatures, terrariums, tools, plugins), grouped by type. */
const t = useV2T()
const items = ref([])
const query = ref("")
const loading = ref(false)
const error = ref("")

const ICONS = { creature: "i-carbon-bot", terrarium: "i-carbon-network-4", tool: "i-carbon-tools", plugin: "i-carbon-plug" }
function typeIcon(type) {
  return ICONS[type] || "i-carbon-cube"
}

const groups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const byType = new Map()
  for (const p of items.value) {
    if (q && !`${p.name} ${p.description || ""}`.toLowerCase().includes(q)) continue
    const type = p.type || "package"
    if (!byType.has(type)) byType.set(type, [])
    byType.get(type).push(p)
  }
  return [...byType].map(([type, list]) => ({ type, items: list }))
})

function normalize(data) {
  if (Array.isArray(data)) return data
  if (!data || typeof data !== "object") return []
  const out = []
  for (const [type, list] of Object.entries(data)) if (Array.isArray(list)) for (const it of list) out.push({ ...it, type })
  return out
}

async function load() {
  loading.value = !items.value.length
  error.value = ""
  try {
    items.value = normalize(await registryAPI.listLocal())
  } catch (err) {
    error.value = errorText(err)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
onShownAgain(load)
</script>
