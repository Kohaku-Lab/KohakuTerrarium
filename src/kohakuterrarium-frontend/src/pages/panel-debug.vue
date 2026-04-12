<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900">
    <!-- Tab bar -->
    <div
      class="flex items-center gap-0.5 px-2 py-1 border-b border-warm-200 dark:border-warm-700 overflow-x-auto shrink-0"
    >
      <button
        v-for="t in tabs"
        :key="t.id"
        class="px-3 py-1 rounded text-xs whitespace-nowrap transition-colors"
        :class="
          active === t.id
            ? 'bg-iolite/15 text-iolite font-medium'
            : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'
        "
        @click="active = t.id"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- Panel body -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <ChatPanel v-if="active === 'chat' && fakeInstance" :instance="fakeInstance" />
      <ActivityPanel v-else-if="active === 'activity'" :instance="fakeInstance" />
      <StatePanel v-else-if="active === 'state'" :instance="fakeInstance" />
      <CreaturesPanel v-else-if="active === 'creatures'" :instance="fakeInstance" />
      <FilesPanel
        v-else-if="active === 'files'"
        :root="fakeInstance?.pwd || '/'"
        :on-select="() => {}"
      />
      <CanvasPanel v-else-if="active === 'canvas'" />
      <SettingsPanel v-else-if="active === 'settings'" :instance="fakeInstance" />
      <DebugPanel v-else-if="active === 'debug'" :instance="fakeInstance" />
      <StatusDashboard v-else-if="active === 'status-dashboard'" :instance="fakeInstance" />
      <StatusBar v-else-if="active === 'status-bar'" />
      <EditorMain v-else-if="active === 'editor'" />
      <div v-else class="h-full flex items-center justify-center text-warm-400 text-sm">
        Select a panel tab above
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import ChatPanel from "@/components/chat/ChatPanel.vue"
import StatusBar from "@/components/chrome/StatusBar.vue"
import EditorMain from "@/components/editor/EditorMain.vue"
import ActivityPanel from "@/components/panels/ActivityPanel.vue"
import CanvasPanel from "@/components/panels/CanvasPanel.vue"
import CreaturesPanel from "@/components/panels/CreaturesPanel.vue"
import DebugPanel from "@/components/panels/DebugPanel.vue"
import FilesPanel from "@/components/panels/FilesPanel.vue"
import SettingsPanel from "@/components/panels/SettingsPanel.vue"
import StatePanel from "@/components/panels/StatePanel.vue"
import StatusDashboard from "@/components/status/StatusDashboard.vue"
import { useInstancesStore } from "@/stores/instances"

const instances = useInstancesStore()
const fakeInstance = computed(
  () =>
    instances.current || {
      id: "demo",
      type: "creature",
      config_name: "demo-agent",
      status: "running",
      pwd: "/tmp",
      model: "demo-model",
      creatures: [],
      channels: [],
    },
)

const tabs = [
  { id: "chat", label: "Chat" },
  { id: "activity", label: "Activity" },
  { id: "state", label: "State" },
  { id: "creatures", label: "Creatures" },
  { id: "files", label: "Files" },
  { id: "canvas", label: "Canvas" },
  { id: "settings", label: "Settings" },
  { id: "debug", label: "Debug" },
  { id: "status-dashboard", label: "StatusDashboard (old)" },
  { id: "status-bar", label: "StatusBar" },
  { id: "editor", label: "EditorMain" },
]

const active = ref("activity")
</script>
