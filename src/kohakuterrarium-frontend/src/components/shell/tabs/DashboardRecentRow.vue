<template>
  <div class="group w-full px-3 py-2 hover:bg-warm-100 dark:hover:bg-warm-900 rounded flex items-center gap-3 text-sm border-b border-warm-100 dark:border-warm-800/50 last:border-b-0">
    <!-- Click anywhere on the body to view; explicit Resume button on
         the right kicks off a new live instance from the session.
         Both actions live side-by-side because "view" and "resume"
         answer different questions and the user wants both. -->
    <button class="flex items-center gap-3 flex-1 min-w-0 text-left" :title="t('shell.recentRow.viewTip')" @click="onView">
      <span class="i-carbon-recently-viewed text-warm-400 shrink-0" />
      <span class="text-warm-500 text-xs shrink-0 w-20">{{ formatDate }}</span>
      <span class="shrink-0 max-w-48 truncate font-medium text-warm-800 dark:text-warm-200">
        {{ sessionId }}
      </span>
      <span v-if="preview" class="text-warm-500 truncate flex-1 text-xs italic"> — {{ preview }} </span>
      <span v-else class="flex-1" />
      <span v-if="turnCount" class="text-xs text-warm-500 shrink-0">{{ turnCount }} turns</span>
      <span v-if="size" class="text-xs text-warm-400 shrink-0">{{ size }}</span>
    </button>
    <div class="flex items-center gap-1 shrink-0">
      <button class="w-7 h-7 inline-flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-iolite/10 dark:hover:bg-iolite/20" :title="t('shell.recentRow.view')" @click="onView">
        <span class="i-carbon-view text-sm" />
      </button>
      <button class="w-7 h-7 inline-flex items-center justify-center rounded text-warm-400 hover:text-aquamarine hover:bg-aquamarine/10 dark:hover:bg-aquamarine/20 disabled:opacity-40 disabled:cursor-not-allowed" :title="t('shell.recentRow.resumeTip')" :disabled="resuming" @click="onResume">
        <span :class="resuming ? 'i-carbon-renew kohaku-pulse' : 'i-carbon-restart'" class="text-sm" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElMessage } from "element-plus"

import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { extractTextPreview } from "@/utils/multimodal"

const props = defineProps({ session: { type: Object, required: true } })
const tabs = useTabsStore()
const instances = useInstancesStore()
const { t } = useI18n()

const resuming = ref(false)

const sessionId = computed(() => props.session.session_name ?? props.session.name ?? "")

const preview = computed(() => extractTextPreview(props.session.preview, 120))

const formatDate = computed(() => {
  const ts = props.session.last_active ?? props.session.modified_at ?? props.session.created_at
  if (!ts) return ""
  const d = typeof ts === "string" ? new Date(ts) : new Date(ts * 1000)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
})

const turnCount = computed(() => props.session.turn_count ?? props.session.turns ?? null)

const size = computed(() => {
  const bytes = props.session.size_bytes ?? props.session.size
  if (!bytes) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
})

function onView() {
  if (!sessionId.value) return
  tabs.openTab({
    kind: "session-viewer",
    id: `session:${sessionId.value}`,
    name: sessionId.value,
  })
}

async function onResume() {
  if (!sessionId.value || resuming.value) return
  resuming.value = true
  try {
    const result = await sessionAPI.resume(sessionId.value)
    await instances.fetchAll()
    const id = result?.instance_id
    if (id) {
      tabs.openTab({
        kind: "attach",
        id: `attach:${id}`,
        target: id,
        config_name: result?.config_name || sessionId.value,
        type: result?.type || result?.kind || "creature",
      })
    }
    ElMessage.success(t("sessions.resumed", { name: sessionId.value }))
  } catch (err) {
    const message = err?.response?.data?.detail || err?.message || String(err)
    ElMessage.error(t("sessions.resumeFailed", { message }))
  } finally {
    resuming.value = false
  }
}
</script>
