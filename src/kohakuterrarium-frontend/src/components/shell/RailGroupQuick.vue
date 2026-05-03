<template>
  <div>
    <div class="px-3 py-1">
      <span class="text-[10px] uppercase tracking-wider text-warm-500 font-medium"> {{ t("shell.rail.quick") }} </span>
    </div>
    <div class="flex flex-col gap-0.5">
      <button v-for="entry in entries" :key="entry.id" class="flex items-center gap-2 px-3 py-1.5 text-sm text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 hover:text-warm-800 dark:hover:text-warm-200 cursor-pointer text-left" @click="entry.action">
        <span :class="entry.icon" class="text-sm shrink-0" />
        <span>{{ entry.label }}</span>
      </button>
    </div>

    <!-- Modals (rendered here so they overlay the whole shell) -->
    <NewCreatureModal v-if="modal === 'creature'" @close="modal = null" />
    <NewTerrariumModal v-if="modal === 'terrarium'" @close="modal = null" />
    <ResumeSessionModal v-if="modal === 'resume'" @close="modal = null" />
    <AdvancedStartModal v-if="modal === 'advanced'" @close="modal = null" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import NewCreatureModal from "@/components/shell/modals/NewCreatureModal.vue"
import NewTerrariumModal from "@/components/shell/modals/NewTerrariumModal.vue"
import ResumeSessionModal from "@/components/shell/modals/ResumeSessionModal.vue"
import AdvancedStartModal from "@/components/shell/modals/AdvancedStartModal.vue"
import GraphEditorTab from "@/components/graph-editor/GraphEditorTab.vue"
import { registerTabKind, tabKindRegistry } from "@/stores/tabKindRegistry"
import { useTabsStore } from "@/stores/tabs"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { buildStudioTabId } from "@/utils/tabsUrl"
import { useI18n } from "@/utils/i18n"

// Register the graph-editor tab kind once at module load. Idempotent
// guard against repeated registrations (HMR / multiple rail mounts).
if (!tabKindRegistry.has("graph-editor")) {
  registerTabKind({ kind: "graph-editor", component: GraphEditorTab })
}

const tabs = useTabsStore()
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
    label: t("shell.quick.newCreature"),
    icon: "i-carbon-add-large",
    action: () => (modal.value = "creature"),
  },
  {
    id: "new-terrarium",
    label: t("shell.quick.newTerrarium"),
    icon: "i-carbon-network-4",
    action: () => (modal.value = "terrarium"),
  },
  {
    id: "resume",
    label: t("shell.quick.resume"),
    icon: "i-carbon-restart",
    action: () => (modal.value = "resume"),
  },
  {
    id: "catalog",
    label: t("shell.quick.catalog"),
    icon: "i-carbon-catalog",
    action: () => tabs.openTab({ kind: "catalog", id: "catalog" }),
  },
  {
    id: "studio",
    label: t("shell.quick.studio"),
    icon: "i-carbon-tool-box",
    action: openStudio,
  },
  {
    id: "sessions",
    label: t("shell.quick.sessions"),
    icon: "i-carbon-list",
    action: () => tabs.openTab({ kind: "saved-sessions", id: "saved-sessions" }),
  },
  {
    id: "stats",
    label: t("shell.quick.stats"),
    icon: "i-carbon-chart-line",
    action: () => tabs.openTab({ kind: "stats", id: "stats" }),
  },
  {
    id: "graph-editor",
    label: "Graph Editor",
    icon: "i-carbon-network-3",
    action: () => tabs.openTab({ kind: "graph-editor", id: "graph-editor" }),
  },
  {
    id: "settings",
    label: t("shell.quick.settings"),
    icon: "i-carbon-settings",
    action: () => tabs.openTab({ kind: "settings", id: "settings" }),
  },
])
</script>
