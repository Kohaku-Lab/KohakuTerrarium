<template>
  <button type="button" class="w-full flex items-center gap-2 pl-8 pr-3 py-1 text-left hover:bg-warm-300/50 dark:hover:bg-warm-700/50 disabled:cursor-wait" :class="busy ? 'opacity-70' : ''" :disabled="busy" :title="row.label" :data-test="`project-session-${session.name}`" @click="open">
    <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="live ? 'bg-iolite' : 'bg-warm-400'" :data-test="live ? 'project-session-running' : undefined" />
    <span class="flex-1 min-w-0 truncate text-[12px] text-warm-700 dark:text-warm-300">{{ row.label }}</span>
    <span class="shrink-0 text-[10px] text-warm-500 tabular-nums">{{ whenLabel(row.lastActive, t) }}</span>
  </button>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElMessage } from "element-plus"

import { historyRow, resumeNode, whenLabel } from "@/components/shell/history/historyRows"
import { useConversationsStore } from "@/stores/conversations"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/**
 * One saved session under a sidebar project. A click opens it the way the
 * Running list and History do: the open tab of a running session, else its
 * chat, else a resume where it last ran.
 */
const props = defineProps({ session: { type: Object, required: true } })
const { t } = useI18n()
const tabs = useTabsStore()
const conversations = useConversationsStore()
const busy = ref(false)

const row = computed(() => historyRow(props.session))
const openRow = computed(() => conversations.rows.find((r) => r.saved_name === props.session.name) || null)
const live = computed(() => conversations.liveRows.find((r) => r.saved_name === props.session.name) || null)

async function open() {
  if (busy.value) return
  busy.value = true
  try {
    if (live.value?.runtime_id) {
      const surfaces = tabs.surfaceTabsForTarget(live.value.runtime_id)
      const last = surfaces.chat?.id ?? surfaces.inspector?.id
      if (last) tabs.activateTab(last)
      else await tabs.openSurface(live.value.runtime_id, "chat", { config_name: live.value.config_name, type: live.value.type })
    } else if (openRow.value) {
      await conversations.openSurface(openRow.value, "chat")
    } else {
      const id = await tabs.createSession({ kind: "resume", sessionName: props.session.name, attachMode: "chat", onNode: resumeNode(props.session, "") })
      if (id) void conversations.fetchAll({ force: true })
    }
  } catch (err) {
    ElMessage.error(t("sessions.resumeFailed", { message: err?.response?.data?.detail || err?.message || String(err) }))
  } finally {
    busy.value = false
  }
}
</script>
