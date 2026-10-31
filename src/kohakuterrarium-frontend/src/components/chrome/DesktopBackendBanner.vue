<template>
  <div v-if="state" class="fixed top-0 inset-x-0 z-50 flex items-center justify-center gap-3 px-3 py-1.5 text-xs border-b" :class="state === 'failed' ? 'bg-coral/15 border-coral/40 text-coral' : 'bg-amber/15 border-amber/40 text-warm-700 dark:text-warm-200'" role="alert" data-test="backend-banner">
    <span class="truncate">{{ state === "restarting" ? t("desktop.backend.restarting") : message || t("desktop.backend.failed") }}</span>
    <template v-if="state === 'failed'">
      <button class="underline shrink-0" data-banner="restart" @click="restartBackend">{{ t("settings.desktop.backendRestart") }}</button>
      <button class="underline shrink-0" data-banner="logs" @click="openLogs">{{ t("settings.desktop.backendLogs") }}</button>
    </template>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue"

import { openLogs, restartBackend } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

const BACKEND_EVENT = "kt:desktop-backend-state"
const SHOWN = new Set(["restarting", "failed"])

const { t } = useI18n()
const state = ref("")
const message = ref("")

function onState(event) {
  const next = event.detail?.state
  state.value = SHOWN.has(next) ? next : ""
  message.value = state.value === "failed" ? String(event.detail?.message || "") : ""
}

onMounted(() => window.addEventListener(BACKEND_EVENT, onState))
onBeforeUnmount(() => window.removeEventListener(BACKEND_EVENT, onState))
</script>
