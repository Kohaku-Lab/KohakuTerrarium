<template>
  <div class="h-full overflow-y-auto bg-warm-50 dark:bg-warm-950">
    <div class="max-w-6xl mx-auto py-6 px-6 space-y-6">
      <!-- Header -->
      <header class="flex items-end justify-between">
        <div>
          <h1 class="text-2xl font-bold text-warm-800 dark:text-warm-200">KohakuTerrarium</h1>
          <p class="text-sm text-warm-500 mt-1">{{ t("shell.dashboard.welcome") }}</p>
        </div>
        <select v-model.number="refreshIntervalMs" class="text-xs rounded px-2 py-1 border border-warm-300 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 text-warm-700 dark:text-warm-300 hover:border-iolite focus:border-iolite focus:outline-none">
          <option :value="0">{{ t("shell.dashboard.autoRefreshOff") }}</option>
          <option :value="5000">{{ t("shell.dashboard.autoRefresh5s") }}</option>
          <option :value="15000">{{ t("shell.dashboard.autoRefresh15s") }}</option>
          <option :value="60000">{{ t("shell.dashboard.autoRefresh60s") }}</option>
        </select>
      </header>

      <!-- Quick start -->
      <DashboardSection :title="t('shell.dashboard.quickStart')">
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <QuickStartCard icon="i-carbon-bot" :label="t('shell.dashboard.startCreature')" :subtitle="t('shell.dashboard.startCreatureSub')" @click="openModal('creature')" />
          <QuickStartCard icon="i-carbon-network-4" :label="t('shell.dashboard.startTerrarium')" :subtitle="t('shell.dashboard.startTerrariumSub')" @click="openModal('terrarium')" />
          <QuickStartCard icon="i-carbon-restart" :label="t('shell.dashboard.resumeQs')" :subtitle="t('shell.dashboard.resumeQsSub')" @click="openModal('resume')" />
          <QuickStartCard icon="i-carbon-settings" :label="t('shell.dashboard.advanced')" :subtitle="t('shell.dashboard.advancedSub')" @click="openModal('advanced')" />
        </div>
      </DashboardSection>

      <!-- Running -->
      <DashboardSection :title="`${t('shell.dashboard.running')} (${instances.running.length})`">
        <div v-if="instances.running.length === 0" class="text-warm-400 italic text-center py-6 text-sm">{{ t("shell.dashboard.noRunning") }}</div>
        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          <DashboardRunningCard v-for="inst in instances.running" :key="inst.id" :instance="inst" />
        </div>
      </DashboardSection>

      <!-- Recent sessions -->
      <DashboardSection :title="`${t('shell.dashboard.recentSessions')}${recentSessions.length ? ` (${recentSessions.length})` : ''}`" :action="showAll ? t('shell.dashboard.showFewer') : t('shell.dashboard.showAll')" @action="showAll = !showAll">
        <div v-if="recentLoading" class="text-warm-400 italic text-center py-3 text-sm">{{ t("shell.dashboard.recentLoading") }}</div>
        <div v-else-if="recentSessions.length === 0" class="text-warm-400 italic text-center py-3 text-sm">{{ t("shell.dashboard.recentEmpty") }}</div>
        <div v-else class="card p-1">
          <DashboardRecentRow v-for="s in displayedSessions" :key="s.session_name ?? s.name" :session="s" />
        </div>
      </DashboardSection>

      <!-- Studio digest -->
      <DashboardStudioCard />

      <!-- At a glance — stats summary, click to open the full Stats tab -->
      <DashboardStatsCard />
    </div>

    <!-- Real start modals (Phase 5) -->
    <NewCreatureModal v-if="modal === 'creature'" @close="modal = null" />
    <NewTerrariumModal v-if="modal === 'terrarium'" @close="modal = null" />
    <ResumeSessionModal v-if="modal === 'resume'" @close="modal = null" />
    <AdvancedStartModal v-if="modal === 'advanced'" @close="modal = null" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"

import DashboardSection from "@/components/shell/tabs/DashboardSection.vue"
import QuickStartCard from "@/components/shell/tabs/QuickStartCard.vue"
import DashboardRunningCard from "@/components/shell/tabs/DashboardRunningCard.vue"
import DashboardRecentRow from "@/components/shell/tabs/DashboardRecentRow.vue"
import DashboardStudioCard from "@/components/shell/tabs/DashboardStudioCard.vue"
import DashboardStatsCard from "@/components/shell/tabs/DashboardStatsCard.vue"
import NewCreatureModal from "@/components/shell/modals/NewCreatureModal.vue"
import NewTerrariumModal from "@/components/shell/modals/NewTerrariumModal.vue"
import ResumeSessionModal from "@/components/shell/modals/ResumeSessionModal.vue"
import AdvancedStartModal from "@/components/shell/modals/AdvancedStartModal.vue"
import { useInstancesStore } from "@/stores/instances"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const instances = useInstancesStore()
const refreshIntervalMs = ref(5000)
const recentSessions = ref([])
const recentLoading = ref(true)
const showAll = ref(false)
const modal = ref(null)
let timer = null

const displayedSessions = computed(() => (showAll.value ? recentSessions.value : recentSessions.value.slice(0, 5)))

function openModal(name) {
  modal.value = name
}

async function refresh() {
  await instances.fetchAll()
  try {
    const data = await sessionAPI.list({ limit: 20 })
    // sessionAPI.list returns ``{sessions, total}`` per pages/sessions/index.vue
    const list = Array.isArray(data) ? data : (data?.sessions ?? data?.items ?? [])
    recentSessions.value = Array.isArray(list) ? list : []
  } catch {
    recentSessions.value = []
  } finally {
    recentLoading.value = false
  }
}

function startTimer() {
  stopTimer()
  if (refreshIntervalMs.value > 0) {
    timer = setInterval(refresh, refreshIntervalMs.value)
  }
}
function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(refreshIntervalMs, startTimer)

onMounted(() => {
  refresh()
  startTimer()
})
onUnmounted(stopTimer)
</script>
