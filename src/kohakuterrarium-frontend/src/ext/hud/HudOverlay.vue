<template>
  <div class="h-full flex flex-col gap-2 p-2 overflow-auto" data-test="hud">
    <button v-for="session in sessions" :key="session.id" type="button" class="w-full text-left rounded-lg px-2.5 py-2 flex flex-col gap-1 hover:bg-warm-100 dark:hover:bg-warm-800" :title="t('hud.open', { name: session.name })" data-hud-session @click="open(session.id)">
      <span class="flex items-center gap-2 min-w-0">
        <span class="w-2 h-2 rounded-full shrink-0" :class="session.running ? 'bg-iolite' : 'bg-warm-400'" />
        <span class="kt-text-body font-medium truncate flex-1 min-w-0">{{ session.name }}</span>
        <span class="kt-text-caption font-mono text-warm-500 shrink-0">{{ formatTokens(session.tokens) }}</span>
      </span>
      <span v-for="creature in session.creatures" :key="creature.name" class="pl-4 flex items-center gap-2 min-w-0 kt-text-caption text-warm-600 dark:text-warm-300" data-hud-creature>
        <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="creature.running ? 'bg-iolite' : 'bg-warm-300 dark:bg-warm-600'" />
        <span class="truncate flex-1 min-w-0">{{ creature.name }}</span>
        <span class="font-mono text-warm-500 shrink-0">{{ formatTokens(creature.tokens) }}</span>
      </span>
    </button>
    <p v-if="loaded && !sessions.length" class="kt-text-caption text-warm-500 text-center py-6" data-hud-empty>{{ t("hud.empty") }}</p>
    <p v-if="failed" class="kt-text-caption text-coral text-center" data-hud-error>{{ t("hud.failed") }}</p>
  </div>
</template>

<script setup>
import { inject, onBeforeUnmount, onMounted, ref } from "vue"

import { hudRows, formatTokens, sessionsToFetch } from "@/ext/hud/hudModel"
import { OVERLAY_MODE_INJECT_KEY } from "@/ext/sdk/keys"
import { openSessionScope } from "@/utils/attentionNavigation"
import { sessionAPI } from "@/utils/api"
import { focusSession } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

/** Running sessions with their creatures and token counts; a click shows the session. */
const POLL_MS = 4000
const { t } = useI18n()
const inWindow = inject(OVERLAY_MODE_INJECT_KEY, "app") === "window"

const sessions = ref([])
const loaded = ref(false)
const failed = ref(false)
let timer = null
let busy = false

async function refresh() {
  if (busy || document.hidden) return
  busy = true
  try {
    const chosen = sessionsToFetch(await sessionAPI.listActive())
    const details = await Promise.all(chosen.map((entry) => sessionAPI.getActive(entry.session_id).catch(() => null)))
    sessions.value = hudRows(details)
    failed.value = false
  } catch {
    failed.value = true
  } finally {
    loaded.value = true
    busy = false
  }
}

function open(id) {
  if (inWindow) void focusSession(id)
  else openSessionScope(id)
}

onMounted(() => {
  void refresh()
  timer = setInterval(refresh, POLL_MS)
})
onBeforeUnmount(() => clearInterval(timer))
</script>
