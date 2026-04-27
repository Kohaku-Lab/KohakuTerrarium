<template>
  <div class="h-full min-h-0 overflow-hidden flex flex-col gap-3 p-4">
    <!-- Search bar + filters -->
    <div class="flex flex-wrap items-center gap-2">
      <el-input v-model="query" :placeholder="t('sessionViewer.find.placeholder')" size="small" clearable style="flex: 1; min-width: 180px" @keydown.enter="runSearch" @clear="onClear">
        <template #prefix><div class="i-carbon-search" /></template>
      </el-input>
      <el-select v-model="mode" size="small" style="width: 120px">
        <el-option value="auto" :label="t('sessionViewer.find.mode.auto')" />
        <el-option value="fts" :label="t('sessionViewer.find.mode.fts')" />
        <el-option value="semantic" :label="t('sessionViewer.find.mode.semantic')" />
        <el-option value="hybrid" :label="t('sessionViewer.find.mode.hybrid')" />
      </el-select>
      <el-select v-if="agents.length > 1" v-model="agent" size="small" clearable style="width: 140px" :placeholder="t('sessionViewer.find.allAgents')">
        <el-option v-for="a in agents" :key="a" :value="a" :label="a" />
      </el-select>
      <el-button size="small" type="primary" :loading="loading" :disabled="!query.trim()" @click="runSearch">{{ t("sessionViewer.find.search") }}</el-button>
    </div>

    <!-- Type chips (post-filter on results) -->
    <div class="flex items-center gap-1 flex-wrap text-[11px]">
      <span class="text-warm-400 mr-1">{{ t("sessionViewer.trace.filters.types") }}:</span>
      <button v-for="chip in TYPE_CHIPS" :key="chip" class="px-1.5 py-0.5 rounded border" :class="activeChip === chip ? 'border-iolite bg-iolite/10 text-iolite' : 'border-warm-300 dark:border-warm-700 text-warm-500 hover:text-warm-700'" @click="toggleChip(chip)">{{ chip }}</button>
    </div>

    <!-- Results -->
    <div class="flex-1 min-h-0 overflow-y-auto">
      <div v-if="error" class="card p-4 text-coral text-sm">{{ error }}</div>
      <div v-else-if="loading && !results.length" class="card p-4 text-secondary text-sm">{{ t("common.loading") }}</div>
      <div v-else-if="!results.length" class="card p-4 text-secondary text-sm">{{ submitted ? t("sessionViewer.find.empty") : t("sessionViewer.find.hint") }}</div>

      <div v-else class="flex flex-col gap-2">
        <div class="text-[11px] text-warm-400">{{ t("sessionViewer.find.foundN", { n: filteredResults.length }) }}</div>
        <button v-for="r in filteredResults" :key="`${r.agent || ''}-${r.round}-${r.block}`" class="text-left card p-2 flex flex-col gap-1 hover:bg-warm-50 dark:hover:bg-warm-800/40 transition-colors" @click="openTurn(r)">
          <div class="flex items-center gap-2 text-[11px] text-warm-400 font-mono">
            <span v-if="r.agent">{{ r.agent }}</span>
            <span v-if="r.block_type" :class="badgeClass(r.block_type)">{{ r.block_type }}</span>
            <span v-if="r.tool_name">tool: {{ r.tool_name }}</span>
            <span v-if="r.channel">ch: {{ r.channel }}</span>
            <span v-if="r.round != null" class="ml-auto">turn {{ r.round }}</span>
          </div>
          <div class="text-[12px] text-warm-700 dark:text-warm-300 whitespace-pre-wrap break-words line-clamp-3">{{ r.content }}</div>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import { useSessionDetailStore } from "@/stores/sessionDetail"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const detail = useSessionDetailStore()
const router = useRouter()
const route = useRoute()

const TYPE_CHIPS = ["text", "tool", "subagent", "user", "assistant"]

const query = ref("")
const mode = ref("auto")
const agent = ref("")
const activeChip = ref("")
const loading = ref(false)
const error = ref("")
const submitted = ref(false)
const results = ref([])

const agents = computed(() => detail.agents || [])

const filteredResults = computed(() => {
  if (!activeChip.value) return results.value
  return results.value.filter((r) => {
    const t2 = String(r.block_type || "").toLowerCase()
    return t2.includes(activeChip.value)
  })
})

function toggleChip(chip) {
  activeChip.value = activeChip.value === chip ? "" : chip
}

function badgeClass(type) {
  const t2 = String(type || "").toLowerCase()
  if (t2.includes("error")) return "text-coral"
  if (t2.includes("tool")) return "text-amber"
  if (t2.includes("subagent")) return "text-iolite"
  return "text-warm-500"
}

async function runSearch() {
  const q = query.value.trim()
  if (!q || !detail.name) return
  loading.value = true
  submitted.value = true
  error.value = ""
  try {
    const data = await sessionAPI.searchMemory(detail.name, {
      q,
      mode: mode.value,
      k: 50,
      agent: agent.value || null,
    })
    results.value = data.results || []
  } catch (e) {
    error.value = `${t("sessionViewer.find.failed")}: ${e?.message || e}`
    results.value = []
  } finally {
    loading.value = false
  }
}

function onClear() {
  results.value = []
  submitted.value = false
  error.value = ""
}

function openTurn(r) {
  if (r.round == null) return
  detail.setTab("trace")
  router.replace({ query: { ...route.query, tab: "trace", turn: r.round } })
}

// Reset on session switch.
watch(
  () => detail.name,
  () => {
    onClear()
    query.value = ""
  },
)
</script>
