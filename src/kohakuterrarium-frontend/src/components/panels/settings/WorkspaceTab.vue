<template>
  <div class="workspace-tab p-4 max-w-2xl flex flex-col gap-3">
    <p class="text-[12px] text-warm-500 leading-relaxed">Switch the directory tools operate against (read / glob / grep / edit / bash). Subagents inherit the change through the shared executor. Path-boundary guard re-roots and read-before-write tracking is cleared.</p>

    <div v-if="!target" class="text-[12px] text-coral">Workspace is only available for root/creature tabs.</div>

    <template v-else>
      <div class="flex flex-col gap-1">
        <label class="text-[11px] text-warm-400">Current directory</label>
        <code class="font-mono text-[12px] text-warm-700 dark:text-warm-300 break-all">{{ currentPwd || "—" }}</code>
      </div>

      <div class="flex flex-col gap-1">
        <label class="text-[11px] text-warm-400">New directory</label>
        <el-input v-model="draft" size="small" :placeholder="currentPwd || '/absolute/path'" :disabled="saving" :list="recent.length ? `ws-recent-${recentKey}` : undefined" />
        <datalist v-if="recent.length" :id="`ws-recent-${recentKey}`">
          <option v-for="p in recent" :key="p" :value="p" />
        </datalist>
        <p class="text-[11px] text-warm-400">Absolute path. ``~`` is expanded server-side.</p>
      </div>

      <div v-if="errorMessage" class="text-[12px] text-coral">{{ errorMessage }}</div>
      <div v-else-if="status" class="text-[12px] text-aquamarine">{{ status }}</div>

      <div v-if="isProcessing" class="text-[11px] text-amber italic">Agent is currently processing — interrupt the turn before switching directory.</div>

      <div class="flex items-center gap-2">
        <el-button size="small" type="primary" :loading="saving" :disabled="!canSave" @click="apply">Switch</el-button>
        <el-button size="small" :disabled="!isDirty || saving" plain @click="reset">Reset</el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, toRefs, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const RECENT_KEY_PREFIX = "kt:recent-cwds:"
const RECENT_MAX = 8

/**
 * Per-creature "Workspace" form. Reads/writes
 * ``/api/sessions/{sid}/creatures/{target}/working-dir`` on the active
 * creature — same routing convention as EnvTab/TriggersTab. ``sid`` is
 * the session/graph id (``instance.graph_id``); ``target`` is a
 * creature name (``chat.terrariumTarget`` for multi-creature graphs,
 * the single creature's name for solo sessions).
 *
 * Suggestions: pulled from ``localStorage`` keyed by ``sid:target``,
 * plus the current pwd, so recent dirs auto-complete via a ``<datalist>``.
 */
const props = defineProps({
  instance: { type: Object, default: null },
})
const { instance } = toRefs(props)

const chat = useChatStore()

const currentPwd = ref("")
const draft = ref("")
const saving = ref(false)
const status = ref("")
const errorMessage = ref("")
const recent = ref([])

const sid = computed(() => instance.value?.graph_id || instance.value?.id || "")
const target = computed(() => {
  const creatures = instance.value?.creatures || []
  if (creatures.length === 0) return null
  if (creatures.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures[0].name
})
const recentKey = computed(() => (sid.value && target.value ? `${sid.value}:${target.value}` : ""))
const isProcessing = computed(() => !!chat.processingByTab?.[chat.activeTab])
const isDirty = computed(() => draft.value && draft.value !== currentPwd.value)
const canSave = computed(() => isDirty.value && !saving.value && !isProcessing.value)

function loadRecent(key) {
  if (!key) return []
  try {
    const raw = localStorage.getItem(RECENT_KEY_PREFIX + key)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((p) => typeof p === "string") : []
  } catch {
    return []
  }
}

function saveRecent(key, list) {
  if (!key) return
  try {
    localStorage.setItem(RECENT_KEY_PREFIX + key, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* noop */
  }
}

function pushRecent(path) {
  const key = recentKey.value
  if (!key || !path) return
  const filtered = recent.value.filter((p) => p !== path)
  filtered.unshift(path)
  recent.value = filtered.slice(0, RECENT_MAX)
  saveRecent(key, recent.value)
}

async function loadCurrent() {
  status.value = ""
  errorMessage.value = ""
  if (!sid.value || !target.value) {
    currentPwd.value = ""
    draft.value = ""
    return
  }
  try {
    const data = await terrariumAPI.getWorkingDir(sid.value, target.value)
    currentPwd.value = data?.pwd || ""
    draft.value = currentPwd.value
    if (currentPwd.value && !recent.value.includes(currentPwd.value)) {
      pushRecent(currentPwd.value)
    }
  } catch (e) {
    currentPwd.value = instance.value?.pwd || ""
    draft.value = currentPwd.value
    errorMessage.value = `Failed to load working dir: ${e?.response?.data?.detail || e?.message || e}`
  }
}

watch(
  recentKey,
  (key) => {
    recent.value = loadRecent(key)
    loadCurrent()
  },
  { immediate: true },
)

async function apply() {
  if (!sid.value || !target.value || !draft.value) return
  saving.value = true
  status.value = ""
  errorMessage.value = ""
  try {
    const data = await terrariumAPI.setWorkingDir(sid.value, target.value, draft.value)
    currentPwd.value = data?.pwd || draft.value
    draft.value = currentPwd.value
    pushRecent(currentPwd.value)
    status.value = `Switched to ${currentPwd.value}`
  } catch (e) {
    errorMessage.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}

function reset() {
  draft.value = currentPwd.value
  errorMessage.value = ""
  status.value = ""
}
</script>
