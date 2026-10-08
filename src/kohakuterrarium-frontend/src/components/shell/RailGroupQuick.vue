<template>
  <div>
    <div class="px-3 py-1">
      <span class="kt-text-caption uppercase tracking-wider text-warm-600 dark:text-warm-500 font-medium"> {{ t("shell.rail.quick") }} </span>
    </div>
    <div class="flex flex-col gap-0.5">
      <button v-for="entry in entries" :key="entry.id" class="flex items-center gap-2 px-3 py-1.5 kt-text-body text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 hover:text-warm-800 dark:hover:text-warm-200 cursor-pointer text-left" @click="entry.action">
        <span :class="entry.icon" class="kt-text-body shrink-0" />
        <span>{{ entry.label }}</span>
      </button>
    </div>

    <!-- Modals (rendered here so they overlay the whole shell) -->
    <NewSessionDialog v-if="modal === 'new'" @close="modal = null" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { useAuthStore } from "@/stores/auth"
import { useTabsStore } from "@/stores/tabs"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { buildStudioTabId } from "@/utils/tabsUrl"
import { useI18n } from "@/utils/i18n"

const tabs = useTabsStore()
const auth = useAuthStore()
const ws = useStudioWorkspaceStore()
const modal = ref(null)
const { t } = useI18n()

function openStudio() {
  // If a workspace is already open, jump straight into its dashboard
  // tab rather than the home picker. Without this we leave a Home tab
  // around that the user has to manually close after picking a
  // workspace.
  if (ws.isOpen && ws.root) {
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "workspace", workspace: ws.root }),
      workspace: ws.root,
      entity: ws.root,
      entityKind: "workspace",
    })
    return
  }
  tabs.openTab({
    kind: "studio-editor",
    id: buildStudioTabId({ entityKind: "home" }),
    workspace: "",
    entity: "home",
    entityKind: "home",
  })
}

// Computed so labels react to locale changes (the rail is mounted
// once for the whole shell — without `computed` we'd be stuck on
// whichever locale was active at first mount).
const entries = computed(() => [
  {
    id: "new",
    label: t("lab.rail.newSession"),
    icon: "i-carbon-add-large",
    action: () => (modal.value = "new"),
  },
  {
    id: "history",
    label: t("lab.history.title"),
    icon: "i-carbon-recently-viewed",
    action: () => tabs.openTab({ kind: "saved-sessions", id: "saved-sessions" }),
  },
  {
    id: "catalog",
    label: t("shell.quick.catalog"),
    icon: "i-carbon-catalog",
    action: () => tabs.openTab({ kind: "catalog", id: "catalog" }),
  },
  {
    id: "extensions",
    label: t("shell.quick.extensions"),
    icon: "i-carbon-plug",
    action: () => tabs.openTab({ kind: "extensions", id: "extensions" }),
  },
  {
    id: "studio",
    label: t("shell.quick.studio"),
    icon: "i-carbon-tool-box",
    action: openStudio,
  },
  {
    id: "stats",
    label: t("shell.quick.stats"),
    icon: "i-carbon-chart-line",
    action: () => tabs.openTab({ kind: "stats", id: "stats" }),
  },
  {
    id: "graph",
    label: t("graph.tab.title"),
    icon: "i-carbon-network-3",
    action: () => tabs.openTab({ kind: "graph", id: "graph" }),
  },
  {
    id: "settings",
    label: t("shell.quick.settings"),
    icon: "i-carbon-settings",
    action: () => tabs.openTab({ kind: "settings", id: "settings" }),
  },
  // Admin portal — only for an admin-role user on a multi-user host.
  // ``isAdmin`` is false on single-user / anonymous hosts, so this
  // entry simply doesn't render there.
  ...(auth.isAdmin
    ? [
        {
          id: "admin",
          label: t("shell.quick.admin"),
          icon: "i-carbon-user-admin",
          action: () => tabs.openTab({ kind: "admin", id: "admin" }),
        },
      ]
    : []),
])
</script>
