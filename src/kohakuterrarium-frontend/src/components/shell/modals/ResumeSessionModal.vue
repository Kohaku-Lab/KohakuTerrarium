<template>
  <ModalShell @close="$emit('close')">
    <template #title>{{ t("shell.modal.resume.title") }}</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <div>
        <input v-model="search" type="text" class="input-field w-full text-sm" :placeholder="t('shell.modal.resume.searchPlaceholder')" />
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1">
          {{ search ? t("shell.modal.resume.matches") : t("shell.modal.resume.recent") }}
        </label>
        <div v-if="loading" class="text-warm-400 italic text-sm py-3 text-center">{{ t("shell.modal.resume.loading") }}</div>
        <div v-else-if="filteredSessions.length === 0" class="text-warm-400 italic text-sm py-3 text-center">
          {{ search ? t("shell.modal.resume.noMatches") : t("shell.modal.resume.empty") }}
        </div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1 pr-1">
          <label v-for="s in filteredSessions" :key="s.session_name ?? s.name" class="flex items-start gap-3 px-3 py-2 rounded cursor-pointer transition-colors border border-transparent" :class="selected === (s.session_name ?? s.name) ? 'bg-iolite/10 border-iolite/40' : 'hover:bg-warm-100 dark:hover:bg-warm-900'">
            <input v-model="selected" type="radio" :value="s.session_name ?? s.name" class="mt-1 accent-iolite" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate">
                {{ s.session_name ?? s.name }}
              </div>
              <div class="text-xs text-warm-500">
                <span v-if="s.last_active">{{ formatDate(s.last_active) }}</span>
                <span v-if="s.turn_count"> · {{ s.turn_count }} turns</span>
                <span v-if="s.size_bytes"> · {{ formatBytes(s.size_bytes) }}</span>
                <span v-if="s.config_type"> · {{ s.config_type }}</span>
              </div>
              <div v-if="previewOf(s)" class="text-xs text-warm-500 dark:text-warm-500 italic line-clamp-2 mt-0.5" :title="previewOf(s, 600)">
                {{ previewOf(s) }}
              </div>
            </div>
          </label>
        </div>
      </div>

      <label class="flex items-center gap-2 text-sm">
        <input v-model="alsoOpenInspector" type="checkbox" class="accent-iolite" />
        {{ t("shell.modal.resume.alsoOpenInspector") }}
      </label>

      <div v-if="errorMsg" class="text-coral text-xs">{{ errorMsg }}</div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="$emit('close')">{{ t("shell.modal.resume.cancel") }}</button>
        <button class="btn-primary text-xs px-3 py-1.5" :disabled="!canSubmit" @click="onSubmit">
          {{ resuming ? t("shell.modal.resume.resuming") : t("shell.modal.resume.resume") }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { extractTextPreview } from "@/utils/multimodal"

const { t } = useI18n()

function previewOf(s, limit = 160) {
  return extractTextPreview(s?.preview, limit)
}

const emit = defineEmits(["close"])

const tabs = useTabsStore()
const allSessions = ref([])
const loading = ref(true)
const search = ref("")
const selected = ref(null)
const alsoOpenInspector = ref(false)
const resuming = ref(false)
const errorMsg = ref("")

onMounted(async () => {
  try {
    const data = await sessionAPI.list({ limit: 50 })
    const list = Array.isArray(data) ? data : (data?.sessions ?? data?.items ?? [])
    allSessions.value = Array.isArray(list) ? list : []
  } catch {
    allSessions.value = []
  } finally {
    loading.value = false
  }
})

const filteredSessions = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return allSessions.value.slice(0, 10)
  return allSessions.value.filter((s) => (s.session_name ?? s.name ?? "").toLowerCase().includes(q))
})

const canSubmit = computed(() => Boolean(selected.value && !resuming.value))

async function onSubmit() {
  if (!canSubmit.value) return
  resuming.value = true
  errorMsg.value = ""
  try {
    await tabs.createSession({
      kind: "resume",
      sessionName: selected.value,
      attachMode: alsoOpenInspector.value ? "both" : "chat",
    })
    emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    resuming.value = false
  }
}

function formatDate(t) {
  const d = typeof t === "string" ? new Date(t) : new Date(t * 1000)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function formatBytes(b) {
  if (!b) return ""
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
</script>
