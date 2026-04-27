<template>
  <div class="h-full min-h-0 flex flex-col overflow-hidden">
    <!-- Header strip — full viewport width so the session name +
         metadata line up with the tabs and content below. -->
    <div class="px-4 py-3 flex items-center gap-3 shrink-0">
      <button class="btn-secondary" @click="goBack"><span class="i-carbon-arrow-left mr-1" /> Back</button>
      <div class="min-w-0 flex-1">
        <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 truncate">{{ sessionName }}</h1>
        <p class="text-secondary text-xs truncate">
          <template v-if="detail.meta">
            {{ detail.meta.config_type || "session" }} · v{{ detail.meta.format_version || 1 }}
            <span v-if="detail.meta.last_active"> · {{ t("sessionViewer.overview.lastActive") }}: {{ formatDate(detail.meta.last_active) }}</span>
          </template>
          <template v-else>—</template>
        </p>
      </div>
    </div>

    <!-- Tab strip — full width so it underlines all tabs across the
         page edge to edge. -->
    <div class="px-4 shrink-0 border-b border-warm-200 dark:border-warm-700">
      <div class="flex gap-0 overflow-x-auto">
        <button v-for="tab in tabs" :key="tab.id" class="px-4 py-2 text-sm whitespace-nowrap border-b-2 transition-colors flex items-center gap-2" :class="detail.activeTab === tab.id ? 'border-iolite text-iolite' : 'border-transparent text-warm-500 hover:text-warm-700 dark:hover:text-warm-300'" @click="selectTab(tab.id)">
          <div :class="tab.icon" class="text-base shrink-0" />
          <span>{{ tab.label }}</span>
        </button>
      </div>
    </div>

    <!-- Body: tree pane + tab content. No max-width — the tree pane
         is a fixed 260px column and the tab content takes the rest of
         the viewport. -->
    <div class="flex-1 min-h-0 overflow-hidden px-4 pb-4 pt-3">
      <div class="h-full min-h-0 grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-3">
        <!-- Tree pane (collapses on small screens) -->
        <div class="hidden lg:block min-h-0 overflow-hidden">
          <SessionTreePane />
        </div>
        <!-- Tab content -->
        <div class="min-h-0 overflow-hidden">
          <SessionDetail />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import SessionDetail from "@/components/sessions/SessionDetail.vue"
import SessionTreePane from "@/components/sessions/SessionTreePane.vue"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const detail = useSessionDetailStore()

const sessionName = computed(() => String(route.params.name || ""))

const tabs = computed(() => [
  { id: "overview", label: t("sessionViewer.tabs.overview"), icon: "i-carbon-dashboard" },
  { id: "trace", label: t("sessionViewer.tabs.trace"), icon: "i-carbon-chart-line" },
  { id: "conv", label: t("sessionViewer.tabs.conv"), icon: "i-carbon-chat" },
  { id: "cost", label: t("sessionViewer.tabs.cost"), icon: "i-carbon-currency-dollar" },
  { id: "find", label: t("sessionViewer.tabs.find"), icon: "i-carbon-search" },
  { id: "diff", label: t("sessionViewer.tabs.diff"), icon: "i-carbon-compare" },
])

function selectTab(tab) {
  detail.setTab(tab)
  router.replace({ query: { ...route.query, tab } })
}

function goBack() {
  router.push("/sessions")
}

function formatDate(iso) {
  if (!iso) return ""
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

watch(
  sessionName,
  (name) => {
    if (!name) return
    detail.load(name)
  },
  { immediate: true },
)

watch(
  () => route.query.tab,
  (q) => {
    if (typeof q === "string") detail.setTab(q)
  },
  { immediate: true },
)

onUnmounted(() => {
  // Detail store is a singleton; leave its state for fast back-nav.
})
</script>
