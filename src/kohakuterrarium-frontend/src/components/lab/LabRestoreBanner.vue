<template>
  <div v-if="running || failed.length || showKilled" class="kt-v2-float kt-v2-edge rounded-xl border shadow-lg w-full max-w-[26rem] overflow-hidden text-[12px]" role="status" data-test="lab-restore">
    <div v-if="running" class="px-3 py-2 flex items-center gap-2 text-warm-700 dark:text-warm-200"><span class="i-carbon-renew animate-spin text-iolite" />{{ t("lab.restore.restoring", { n: pending }) }}</div>
    <template v-else>
      <div v-if="showKilled" class="px-3 py-2 flex flex-col gap-1" data-test="lab-restore-killed">
        <div class="flex items-center gap-2 text-amber-shadow dark:text-amber-light">
          <span class="i-carbon-time" />
          <span class="flex-1">{{ t("lab.restore.killed", { n: killedCount }) }}</span>
          <button type="button" class="shrink-0 h-6 px-2 rounded-md text-[11px] text-warm-500 hover:text-warm-800 dark:hover:text-warm-100" data-test="lab-restore-killed-dismiss" @click="hideKilled">{{ t("lab.restore.dismiss") }}</button>
        </div>
        <div v-for="item in killed" :key="`${item.sessionId}:${item.agent}`" class="text-[11px] text-warm-600 dark:text-warm-300 truncate" :title="item.jobs.join(', ')">
          <span class="font-medium">{{ item.agent }}</span
          >: <span class="font-mono">{{ item.jobs.join(", ") }}</span>
        </div>
      </div>
      <div v-if="failed.length" class="px-3 py-2 flex items-center gap-2 text-coral" :class="showKilled ? 'kt-v2-line border-t' : ''"><span class="i-carbon-warning-alt" />{{ t("lab.restore.failed", { n: failed.length }) }}</div>
      <ul v-if="failed.length" class="kt-v2-line border-t">
        <li v-for="row in failed" :key="row.path" class="flex items-center gap-2 px-3 py-1.5" :data-test="`lab-restore-${row.session_id}`">
          <div class="min-w-0 flex-1">
            <div class="font-mono text-[11px] text-warm-700 dark:text-warm-200 truncate" :title="row.path">{{ fileName(row.path) }}</div>
            <div class="text-[10px] text-warm-500 truncate" :title="row.failed">{{ row.failed }}</div>
          </div>
          <button type="button" class="shrink-0 h-6 px-2 rounded-md text-[11px] bg-iolite/10 text-iolite dark:text-iolite-light hover:bg-iolite hover:text-white disabled:opacity-50" :disabled="busy === row.path" data-test="lab-restore-retry" @click="retry(row)">{{ t("lab.restore.retry") }}</button>
          <button type="button" class="shrink-0 h-6 px-2 rounded-md text-[11px] text-warm-500 hover:text-warm-800 dark:hover:text-warm-100" :disabled="busy === row.path" data-test="lab-restore-dismiss" @click="dismiss(row)">{{ t("lab.restore.dismiss") }}</button>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { ElMessage } from "element-plus"

import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * After a server restart: "restoring N sessions" while the server brings
 * running sessions back, then the tool / sub-agent jobs the restart killed
 * (their creatures were told; Dismiss hides the list in this browser) and any
 * session it could not restore, each with Retry (resume it now) and Dismiss
 * (stop trying). Shows nothing otherwise.
 */
const emit = defineEmits(["restored"])
const POLL_MS = 2000
const KILLED_SEEN_KEY = "kt.lab.killedJobsSeen"

const { t } = useI18n()
const state = ref(null)
const busy = ref("")
const killedSeen = ref(readKilledSeen())
let timer = null

const running = computed(() => !!state.value?.running)
const failed = computed(() => (state.value?.rows || []).filter((r) => r.failed))
const pending = computed(() => (state.value?.rows || []).filter((r) => !r.restored_this_boot && !r.failed).length)
const killed = computed(() =>
  (state.value?.outcomes || []).flatMap((o) =>
    Object.entries(o.killed || {}).map(([agent, jobs]) => ({
      sessionId: o.session_id,
      agent,
      ids: jobs.map((j) => j.job_id),
      jobs: jobs.map((j) => (j.kind === "subagent" ? `${j.name} (sub-agent)` : j.name)),
    })),
  ),
)
const killedCount = computed(() => killed.value.reduce((n, item) => n + item.jobs.length, 0))
const killedSignature = computed(() =>
  killed.value
    .flatMap((item) => item.ids)
    .sort()
    .join(","),
)
const showKilled = computed(() => killedCount.value > 0 && killedSeen.value !== killedSignature.value)

function readKilledSeen() {
  try {
    return localStorage.getItem(KILLED_SEEN_KEY) || ""
  } catch {
    return ""
  }
}

function hideKilled() {
  killedSeen.value = killedSignature.value
  try {
    localStorage.setItem(KILLED_SEEN_KEY, killedSignature.value)
  } catch {
    /* hidden for this page only */
  }
}

function fileName(path) {
  return String(path || "")
    .split(/[\\/]/)
    .pop()
}

async function load() {
  try {
    state.value = await sessionAPI.getRestoreState()
  } catch {
    state.value = null
  }
  clearTimeout(timer)
  if (state.value?.running) timer = setTimeout(load, POLL_MS)
  else if (state.value) emit("restored")
}

async function retry(row) {
  busy.value = row.path
  try {
    const outcome = await sessionAPI.retryRestore(row.path)
    if (outcome?.status === "restored") ElMessage.success(t("lab.restore.retried"))
    else if (outcome?.error) ElMessage.error(outcome.error)
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  } finally {
    busy.value = ""
    await load()
  }
}

async function dismiss(row) {
  busy.value = row.path
  try {
    await sessionAPI.dismissRestore(row.path)
  } finally {
    busy.value = ""
    await load()
  }
}

onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))
</script>
