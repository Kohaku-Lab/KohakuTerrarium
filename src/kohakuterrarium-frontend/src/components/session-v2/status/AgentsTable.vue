<template>
  <StatusSection id="agents" :title="t('status.agents')" icon="i-carbon-bot" :count="rows.length" :empty="!rows.length" :empty-label="t('status.none.agents')">
    <div v-if="error" class="px-4 py-2 text-xs text-coral">{{ error }}</div>
    <div v-if="isCompact" data-test="status-agents-phone">
      <button v-for="row in rows" :key="row.name" type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-14 flex items-center gap-3 px-4 py-2 border-b last:border-b-0 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`status-agent-${row.name}`" @click="ctx.openSheet('agent', { name: row.name })">
        <span v-if="row.job" class="i-carbon-circle-dash animate-spin text-aquamarine shrink-0" />
        <StatusDot v-else :status="row.status" />
        <span class="flex-1 min-w-0">
          <span class="flex items-center gap-1.5 min-w-0">
            <span class="truncate text-sm font-medium text-warm-800 dark:text-warm-100">{{ row.name }}</span>
            <span v-if="row.privileged" class="i-carbon-security text-iolite shrink-0" />
            <span v-if="row.status !== 'running'" class="shrink-0 px-1.5 rounded bg-warm-100 dark:bg-warm-800 text-[10px] text-warm-500">{{ t(`status.state.${row.status}`) }}</span>
          </span>
          <span class="flex items-center gap-2 min-w-0 text-xs text-warm-500">
            <span class="truncate font-mono">{{ row.model || "—" }}</span>
            <span class="shrink-0 font-mono">{{ formatTokens(row.tokens) }}</span>
          </span>
        </span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
    </div>
    <table v-else class="w-full text-xs">
      <thead class="text-warm-400 text-left">
        <tr class="border-b kt-v2-line">
          <th class="font-medium px-4 py-2">{{ t("status.col.name") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.status") }}</th>
          <th class="font-medium px-2 py-2 w-64">{{ t("status.col.model") }}</th>
          <th class="font-medium px-2 py-2 text-right">{{ t("status.col.tokens") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.job") }}</th>
          <th class="px-4 py-2" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.name" class="border-b last:border-b-0 kt-v2-line hover:bg-warm-50 dark:hover:bg-warm-800/40" :data-test="`status-agent-${row.name}`">
          <td class="px-4 py-2">
            <div class="flex items-center gap-2 min-w-0">
              <StatusDot :status="row.status" />
              <span class="font-medium text-warm-800 dark:text-warm-100 truncate">{{ row.name }}</span>
              <span v-if="row.privileged" class="i-carbon-security text-iolite shrink-0" />
              <SiteChip :node-id="row.homeNode" />
            </div>
          </td>
          <td class="px-2 py-2">
            <span class="px-1.5 py-0.5 rounded" :class="row.status === 'running' ? 'bg-aquamarine/10 text-aquamarine-shadow dark:text-aquamarine-light' : 'bg-warm-100 dark:bg-warm-800 text-warm-500'">{{ t(`status.state.${row.status}`) }}</span>
          </td>
          <td class="px-2 py-2">
            <el-select v-if="models.length" :model-value="row.model" size="small" filterable class="w-full" :placeholder="t('status.selectModel')" @change="(m) => switchModel(row, m)">
              <el-option v-for="m in models" :key="m" :label="m" :value="m" />
            </el-select>
            <span v-else class="font-mono text-warm-500 truncate">{{ row.model || "—" }}</span>
          </td>
          <td class="px-2 py-2 text-right font-mono text-warm-600 dark:text-warm-300">{{ formatTokens(row.tokens) }}</td>
          <td class="px-2 py-2">
            <span v-if="row.job" class="inline-flex items-center gap-1 text-warm-600 dark:text-warm-300"><span class="i-carbon-circle-dash animate-spin text-aquamarine" />{{ row.job.name }}</span>
            <span v-else class="text-warm-300 dark:text-warm-600">—</span>
          </td>
          <td class="px-4 py-2">
            <div class="flex items-center justify-end gap-1.5">
              <button v-if="row.status === 'running'" class="kt-v2-b px-2 py-0.5 rounded border border-coral/30 text-coral hover:bg-coral/10 disabled:opacity-50" :disabled="!!busy[row.name]" @click="lifecycle(row, 'stop')">{{ t("status.stop") }}</button>
              <button v-else class="kt-v2-b px-2 py-0.5 rounded border border-aquamarine/40 text-aquamarine-shadow dark:text-aquamarine-light hover:bg-aquamarine/10 disabled:opacity-50" :disabled="!!busy[row.name]" @click="lifecycle(row, 'start')">{{ t("status.start") }}</button>
              <button class="px-2 py-0.5 rounded border kt-v2-edge text-warm-700 dark:text-warm-200 hover:text-iolite hover:border-iolite/40" @click="openChat(row.key)">{{ t("status.openChat") }}</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </StatusSection>
</template>

<script setup>
import { computed, onActivated, reactive, ref } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import StatusDot from "@/components/common/StatusDot.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { agentRows, formatTokens } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import StatusSection from "@/components/session-v2/status/StatusSection.vue"
import { useDensity } from "@/composables/useDensity"
import { configAPI, terrariumAPI } from "@/utils/api"

/**
 * Every creature of the session: status, model (switchable), tokens,
 * current job, start/stop and a jump to its chat. On phones each creature
 * is a row that opens its agent sheet.
 */
const ctx = useSessionV2()
const t = useV2T()
const { isCompact } = useDensity()
const chat = ctx.chat

const busy = reactive({})
const error = ref("")
const models = ref([])

const rows = computed(() =>
  agentRows(ctx.instance.value, {
    tokenUsage: chat.tokenUsage,
    runningJobs: chat.runningJobs,
    modelByTab: chat.modelByTab,
    rootName: chat._rootSourceName,
  }),
)

async function loadModels() {
  try {
    const list = await configAPI.getModels()
    models.value = (list || []).filter((m) => m.available !== false).map((m) => `${m.provider || m.login_provider || ""}/${m.name}`)
  } catch {
    models.value = []
  }
}
onActivated(loadModels)

const refresh = () => ctx.refresh()

async function lifecycle(row, verb) {
  if (busy[row.name]) return
  busy[row.name] = true
  error.value = ""
  try {
    if (verb === "start") await terrariumAPI.startCreature(ctx.sessionId.value, row.name)
    else await terrariumAPI.stopCreature(ctx.sessionId.value, row.name)
    await refresh()
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    busy[row.name] = false
  }
}

async function switchModel(row, model) {
  if (!model || model === row.model) return
  error.value = ""
  try {
    await terrariumAPI.switchCreatureModel(ctx.sessionId.value, row.name, model)
    await refresh()
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  }
}

function openChat(key) {
  chat.openTab(key)
  ctx.setTab("chat")
}
</script>
