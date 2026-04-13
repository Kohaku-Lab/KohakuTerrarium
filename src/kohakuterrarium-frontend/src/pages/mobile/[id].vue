<template>
  <div class="h-full flex flex-col overflow-hidden bg-warm-50 dark:bg-warm-950">
    <!-- Header -->
    <div class="flex items-center gap-2 px-2 h-11 border-b border-warm-200 dark:border-warm-700 shrink-0 bg-white dark:bg-warm-900">
      <MobileNav />
      <div :class="instance?.type === 'terrarium' ? 'i-carbon-network-4 text-taaffeite' : 'i-carbon-bot text-iolite'" class="text-base shrink-0" />
      <span class="text-sm font-medium text-warm-700 dark:text-warm-200 truncate flex-1">{{ instance?.config_name || "Loading..." }}</span>
      <span class="w-2 h-2 rounded-full shrink-0" :class="instance?.status === 'running' ? 'bg-aquamarine' : 'bg-warm-400'" />
      <button class="w-8 h-8 flex items-center justify-center rounded transition-colors" :class="showStatus ? 'text-iolite bg-iolite/10' : 'text-warm-400 hover:text-iolite'" title="Status" @click="showStatus = !showStatus">
        <div class="i-carbon-information text-base" />
      </button>
      <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-iolite transition-colors" title="Desktop view" @click="goDesktop">
        <div class="i-carbon-laptop text-base" />
      </button>
    </div>

    <!-- Status overlay (slide-down) -->
    <div v-if="showStatus" class="border-b border-warm-200 dark:border-warm-700 shrink-0 overflow-hidden" style="height: 240px">
      <StatusDashboardTab :instance="instance" :on-open-tab="handleOpenTab" />
    </div>

    <!-- File overlay (for editor tab) -->
    <Transition name="m-files">
      <div v-if="showFiles" class="absolute inset-0 z-40 flex flex-col bg-white dark:bg-warm-900" style="top: 0; bottom: 52px">
        <div class="flex items-center justify-between px-4 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
          <span class="text-xs font-medium text-warm-500">Files</span>
          <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600" @click="showFiles = false">
            <div class="i-carbon-close text-sm" />
          </button>
        </div>
        <div class="flex-1 min-h-0 overflow-y-auto">
          <FilesPanel :root="instance?.pwd || ''" :on-select="onFileSelect" />
        </div>
      </div>
    </Transition>

    <!-- Panel body -->
    <div class="flex-1 min-h-0 overflow-hidden relative">
      <template v-if="instance">
        <ChatPanel v-show="activeTab === 'chat'" :instance="instance" />
        <TerminalPanel v-if="activeTab === 'terminal'" :instance="instance" />
        <StatePanel v-show="activeTab === 'state'" :instance="instance" />
        <div v-if="activeTab === 'editor'" class="h-full w-full relative">
          <EditorMain />
          <button class="absolute top-2 right-2 z-20 w-10 h-10 flex items-center justify-center rounded-lg bg-warm-100 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 shadow-sm text-warm-500 hover:text-iolite" @click="showFiles = !showFiles">
            <div class="i-carbon-folder text-lg" />
          </button>
        </div>
        <CanvasPanel v-if="activeTab === 'canvas'" />
        <DebugPanel v-if="activeTab === 'debug'" :instance="instance" />
        <CreaturesPanel v-if="activeTab === 'creatures'" :instance="instance" />
      </template>
      <div v-else class="h-full flex items-center justify-center text-warm-400 text-sm">Loading...</div>
    </div>

    <!-- Bottom tab bar -->
    <div class="flex items-center border-t border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 shrink-0" style="padding-bottom: env(safe-area-inset-bottom, 0px)">
      <button v-for="tab in visibleTabs" :key="tab.id" class="flex-1 flex flex-col items-center gap-0.5 py-2 min-w-0 transition-colors" :class="activeTab === tab.id ? 'text-iolite' : 'text-warm-400'" @click="switchTab(tab.id)">
        <div :class="tab.icon" class="text-lg" />
        <span class="text-[9px] leading-tight truncate">{{ tab.label }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import MobileNav from "@/components/mobile/MobileNav.vue"
import ChatPanel from "@/components/chat/ChatPanel.vue"
import EditorMain from "@/components/editor/EditorMain.vue"
import CanvasPanel from "@/components/panels/CanvasPanel.vue"
import CreaturesPanel from "@/components/panels/CreaturesPanel.vue"
import DebugPanel from "@/components/panels/DebugPanel.vue"
import FilesPanel from "@/components/panels/FilesPanel.vue"
import StatePanel from "@/components/panels/StatePanel.vue"
import TerminalPanel from "@/components/panels/TerminalPanel.vue"
import StatusDashboardTab from "@/components/status/StatusDashboardTab.vue"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { useInstancesStore } from "@/stores/instances"

const switchToDesktop = inject("switchToDesktop", null)
const route = useRoute()
const instances = useInstancesStore()
const chat = useChatStore()
const editor = useEditorStore()

const instance = computed(() => instances.current)
const activeTab = ref("chat")
const showStatus = ref(false)
const showFiles = ref(false)

const ALL_TABS = [
  { id: "chat", label: "Chat", icon: "i-carbon-chat" },
  { id: "terminal", label: "Term", icon: "i-carbon-terminal" },
  { id: "state", label: "State", icon: "i-carbon-notebook" },
  { id: "editor", label: "Editor", icon: "i-carbon-code" },
  { id: "canvas", label: "Canvas", icon: "i-carbon-paint-brush" },
  { id: "creatures", label: "Agents", icon: "i-carbon-network-4", terrarium: true },
  { id: "debug", label: "Debug", icon: "i-carbon-debug" },
]

const visibleTabs = computed(() => {
  const isTerr = instance.value?.type === "terrarium"
  return ALL_TABS.filter((t) => {
    if (t.terrarium) return isTerr
    return true
  })
})

function switchTab(id) {
  activeTab.value = id
  showStatus.value = false
  if (id !== "editor") showFiles.value = false
}

function goDesktop() {
  if (switchToDesktop) switchToDesktop()
}

function handleOpenTab(tabKey) {
  chat.openTab(tabKey)
}

function onFileSelect(path) {
  editor.openFile(path)
  showFiles.value = false
}

async function loadInstance() {
  const id = route.params.id
  if (!id) return
  await instances.fetchOne(id)
  if (instance.value) {
    chat.initForInstance(instance.value)
  }
}

onMounted(() => loadInstance())

watch(
  () => route.params.id,
  () => loadInstance(),
)
</script>

<style scoped>
.m-files-enter-active,
.m-files-leave-active {
  transition:
    transform 0.2s ease,
    opacity 0.15s ease;
}
.m-files-enter-from,
.m-files-leave-to {
  transform: translateX(-100%);
  opacity: 0;
}
</style>
