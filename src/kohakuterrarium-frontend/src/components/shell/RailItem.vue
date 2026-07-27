<template>
  <div class="group flex items-center gap-2 px-3 py-1.5 hover:bg-warm-300/50 dark:hover:bg-warm-700/50 cursor-pointer" :class="{ 'opacity-70': resuming }" @click="onRowClick" @contextmenu.prevent="showMenu = true">
    <!-- Status dot -->
    <span class="w-2 h-2 rounded-full shrink-0" :class="statusColor" />

    <!-- Name (truncates) -->
    <span class="flex-1 kt-text-body truncate text-warm-800 dark:text-warm-200">
      {{ instance.config_name }}
    </span>

    <!-- Surface indicators — Chat / Inspector -->
    <button class="w-5 h-5 flex items-center justify-center rounded text-[10px] font-mono shrink-0 disabled:cursor-wait" :disabled="resuming" :class="chatOpen ? 'bg-iolite text-white' : 'text-warm-400 hover:text-warm-700 hover:bg-warm-300 dark:hover:bg-warm-700'" :title="chatOpen ? t('shell.rail.closeChat') : t('shell.rail.openChat')" @click.stop="toggleChat">C</button>
    <button class="w-5 h-5 flex items-center justify-center rounded text-[10px] font-mono shrink-0 disabled:cursor-wait" :disabled="resuming" :class="inspectorOpen ? 'bg-iolite text-white' : 'text-warm-400 hover:text-warm-700 hover:bg-warm-300 dark:hover:bg-warm-700'" :title="inspectorOpen ? t('shell.rail.closeInspector') : t('shell.rail.openInspector')" @click.stop="toggleInspector">I</button>

    <RailContextMenu v-if="showMenu" :instance="instance" :chat-open="chatOpen" :inspector-open="inspectorOpen" @close="showMenu = false" @toggle-chat="toggleChat" @toggle-inspector="toggleInspector" @detach="onDetach" @end="endConversation" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElMessage } from "element-plus"

import RailContextMenu from "@/components/shell/RailContextMenu.vue"
import { useConversationsStore } from "@/stores/conversations"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ instance: { type: Object, required: true } })
const tabs = useTabsStore()
const conversations = useConversationsStore()
const { t } = useI18n()
const showMenu = ref(false)

const runtimeTarget = computed(() => (props.instance.is_live ? props.instance.runtime_id : null))
const surfaces = computed(() => (runtimeTarget.value ? tabs.surfaceTabsForTarget(runtimeTarget.value) : {}))
const chatOpen = computed(() => Boolean(surfaces.value.chat))
const inspectorOpen = computed(() => Boolean(surfaces.value.inspector))
const resuming = computed(() => conversations.isResuming(props.instance.id))

const statusColor = computed(() => {
  if (!props.instance.is_live) return "bg-warm-400"
  return (
    {
      running: "bg-iolite",
      paused: "bg-amber",
      stopped: "bg-warm-400",
      errored: "bg-coral",
    }[props.instance.status] ?? "bg-warm-400"
  )
})

async function onRowClick() {
  if (resuming.value) return
  // Activate the most-recently-active surface tab if any open;
  // else open chat as the default landing.
  const lastSurface = surfaces.value.chat?.id ?? surfaces.value.inspector?.id
  if (lastSurface) tabs.activateTab(lastSurface)
  else await toggleChat()
}

async function toggleChat() {
  if (resuming.value) return
  if (chatOpen.value) tabs.closeSurface(runtimeTarget.value, "chat")
  else await openSurface("chat")
}

async function toggleInspector() {
  if (resuming.value) return
  if (inspectorOpen.value) tabs.closeSurface(runtimeTarget.value, "inspector")
  else await openSurface("inspector")
}

async function openSurface(surface) {
  try {
    if (runtimeTarget.value) {
      await tabs.openSurface(runtimeTarget.value, surface, {
        config_name: props.instance.config_name,
        type: props.instance.type,
      })
    } else {
      await conversations.openSurface(props.instance, surface)
    }
  } catch (err) {
    const message = err?.response?.data?.detail || err?.message || String(err)
    ElMessage.error(t("sessions.resumeFailed", { message }))
  }
}

function onDetach() {
  if (runtimeTarget.value) tabs.detach(runtimeTarget.value)
  showMenu.value = false
}

async function endConversation() {
  if (!window.confirm(t("shell.rail.endConfirm"))) return
  try {
    await conversations.endConversation(props.instance)
    if (runtimeTarget.value) tabs.detach(runtimeTarget.value)
  } catch (error) {
    console.error(t("shell.rail.endError"), error)
    window.alert(t("shell.rail.endError"))
  }
}
</script>
