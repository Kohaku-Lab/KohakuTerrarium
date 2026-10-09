<template>
  <div class="flex flex-col gap-2" data-test="history-quote">
    <div v-if="loading" class="text-[11px] text-warm-500">{{ t("sessions.loading") }}</div>
    <div v-else-if="error" class="text-[11px] text-coral" role="alert">{{ error }}</div>
    <div v-else-if="!exchanges.length" class="text-[11px] text-warm-500">{{ t("lab.history.noExchanges") }}</div>
    <div v-for="e in exchanges" :key="e.turn" class="flex flex-col gap-1" :data-test="`history-exchange-${e.turn}`">
      <div class="flex items-start gap-2">
        <span class="shrink-0 w-14 pt-0.5 text-[10px] font-mono text-warm-400">{{ t("lab.history.turnN", { n: e.turn }) }}</span>
        <blockquote class="min-w-0 flex-1 m-0 pl-2 border-l-2 border-iolite/60 text-[12px] text-warm-800 dark:text-warm-100 whitespace-pre-wrap break-words line-clamp-3">{{ e.user }}</blockquote>
      </div>
      <div class="flex items-start gap-2">
        <span class="shrink-0 w-14" />
        <p class="min-w-0 flex-1 m-0 pl-2 border-l-2 border-warm-300 dark:border-warm-600 text-[12px] text-warm-600 dark:text-warm-300 whitespace-pre-wrap break-words line-clamp-4" :class="e.reply ? '' : 'italic text-warm-400'">{{ e.reply || t("lab.history.noReply") }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue"

import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * The last few prompts and replies of a saved session, quoted — what the
 * session was doing when it was last used. Loads when shown.
 */
const props = defineProps({
  sessionKey: { type: String, required: true },
  limit: { type: Number, default: 3 },
  reloadKey: { type: [String, Number], default: "" },
})

const { t } = useI18n()
const exchanges = ref([])
const loading = ref(false)
const error = ref("")
let request = 0

async function load() {
  const mine = ++request
  loading.value = true
  error.value = ""
  try {
    const data = await sessionAPI.getExchanges(props.sessionKey, props.limit)
    if (mine === request) exchanges.value = data?.exchanges || []
  } catch (err) {
    if (mine === request) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (mine === request) loading.value = false
  }
}

watch(() => [props.sessionKey, props.reloadKey], load)
onMounted(load)
</script>
