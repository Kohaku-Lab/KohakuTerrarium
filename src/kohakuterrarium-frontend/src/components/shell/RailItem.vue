<template>
  <div class="group flex items-center gap-2 px-3 py-1.5 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 cursor-pointer" @click="onRowClick" @contextmenu.prevent="showMenu = true">
    <!-- Status dot -->
    <span class="w-2 h-2 rounded-full shrink-0" :class="statusColor" />

    <!-- Name (truncates) -->
    <span class="flex-1 text-sm truncate text-warm-800 dark:text-warm-200">
      {{ instance.config_name }}
    </span>

    <!-- Surface indicators — Chat / Inspector -->
    <button class="w-5 h-5 flex items-center justify-center rounded text-[10px] font-mono shrink-0" :class="chatOpen ? 'bg-iolite text-white' : 'text-warm-400 hover:text-warm-700 hover:bg-warm-300 dark:hover:bg-warm-700'" :title="chatOpen ? 'Close chat tab' : 'Open chat tab'" @click.stop="toggleChat">C</button>
    <button class="w-5 h-5 flex items-center justify-center rounded text-[10px] font-mono shrink-0" :class="inspectorOpen ? 'bg-iolite text-white' : 'text-warm-400 hover:text-warm-700 hover:bg-warm-300 dark:hover:bg-warm-700'" :title="inspectorOpen ? 'Close inspector tab' : 'Open inspector tab'" @click.stop="toggleInspector">I</button>

    <RailContextMenu v-if="showMenu" :instance="instance" :chat-open="chatOpen" :inspector-open="inspectorOpen" @close="showMenu = false" @toggle-chat="toggleChat" @toggle-inspector="toggleInspector" @detach="onDetach" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import RailContextMenu from "@/components/shell/RailContextMenu.vue"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({ instance: { type: Object, required: true } })
const tabs = useTabsStore()
const showMenu = ref(false)

const surfaces = computed(() => tabs.surfaceTabsForTarget(props.instance.id))
const chatOpen = computed(() => Boolean(surfaces.value.chat))
const inspectorOpen = computed(() => Boolean(surfaces.value.inspector))

const statusColor = computed(
  () =>
    ({
      running: "bg-iolite",
      paused: "bg-amber",
      stopped: "bg-warm-400",
      errored: "bg-coral",
    })[props.instance.status] ?? "bg-warm-400",
)

function onRowClick() {
  // Activate the most-recently-active surface tab if any open;
  // else open chat as the default landing.
  const lastSurface = surfaces.value.chat?.id ?? surfaces.value.inspector?.id
  if (lastSurface) tabs.activateTab(lastSurface)
  else toggleChat()
}

function toggleChat() {
  if (chatOpen.value) tabs.closeSurface(props.instance.id, "chat")
  else
    tabs.openSurface(props.instance.id, "chat", {
      config_name: props.instance.config_name,
      type: props.instance.type,
    })
}

function toggleInspector() {
  if (inspectorOpen.value) tabs.closeSurface(props.instance.id, "inspector")
  else
    tabs.openSurface(props.instance.id, "inspector", {
      config_name: props.instance.config_name,
      type: props.instance.type,
    })
}

function onDetach() {
  tabs.detach(props.instance.id)
  showMenu.value = false
}
</script>
