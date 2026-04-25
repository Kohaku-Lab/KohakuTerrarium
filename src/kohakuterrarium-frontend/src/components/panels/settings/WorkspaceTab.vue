<template>
  <div class="workspace-tab p-4 max-w-2xl flex flex-col gap-3">
    <p class="text-[12px] text-warm-500 leading-relaxed">Switch the directory tools operate against (read / glob / grep / edit / bash). Subagents inherit the change through the shared executor. Path-boundary guard re-roots and read-before-write tracking is cleared.</p>

    <div class="flex flex-col gap-1">
      <label class="text-[11px] text-warm-400">Current directory</label>
      <code class="font-mono text-[12px] text-warm-700 dark:text-warm-300 break-all">{{ currentPwd || "—" }}</code>
    </div>

    <div class="flex flex-col gap-1">
      <label class="text-[11px] text-warm-400">New directory</label>
      <el-input v-model="draft" size="small" :placeholder="currentPwd || '/absolute/path'" :disabled="saving" :list="recent.length ? `ws-recent-${agentId}` : undefined" />
      <datalist v-if="recent.length" :id="`ws-recent-${agentId}`">
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
  </div>
</template>

<script setup>
import { computed, ref, toRefs, watch } from "vue"

import { useChatStore } from "@/stores/chat"

const RECENT_KEY_PREFIX = "kt:recent-cwds:"
const RECENT_MAX = 8

/**
 * Per-instance "Workspace" form. Reads/writes
 * ``/api/agents/{id}/working-dir`` on the running agent — no global
 * state, no relation to the Studio workspace store.
 *
 * Suggestions: pulled from ``localStorage`` keyed by agent id, plus the
 * current pwd, so recent dirs auto-complete via a ``<datalist>``.
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

const agentId = computed(() => instance.value?.agent_id || instance.value?.id || "")
const isProcessing = computed(() => !!chat.processingByTab?.[chat.activeTab])
const isDirty = computed(() => draft.value && draft.value !== currentPwd.value)
const canSave = computed(() => isDirty.value && !saving.value && !isProcessing.value)

function loadRecent(id) {
  if (!id) return []
  try {
    const raw = localStorage.getItem(RECENT_KEY_PREFIX + id)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((p) => typeof p === "string") : []
  } catch {
    return []
  }
}

function saveRecent(id, list) {
  if (!id) return
  try {
    localStorage.setItem(RECENT_KEY_PREFIX + id, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* noop */
  }
}

function pushRecent(path) {
  const id = agentId.value
  if (!id || !path) return
  const filtered = recent.value.filter((p) => p !== path)
  filtered.unshift(path)
  recent.value = filtered.slice(0, RECENT_MAX)
  saveRecent(id, recent.value)
}

async function loadCurrent() {
  status.value = ""
  errorMessage.value = ""
  if (!agentId.value) {
    currentPwd.value = ""
    draft.value = ""
    return
  }
  try {
    const res = await fetch(`/api/agents/${encodeURIComponent(agentId.value)}/working-dir`)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    currentPwd.value = data.pwd || ""
    draft.value = currentPwd.value
    if (currentPwd.value && !recent.value.includes(currentPwd.value)) {
      pushRecent(currentPwd.value)
    }
  } catch (e) {
    // Fall back to whatever pwd shipped on the instance object.
    currentPwd.value = instance.value?.pwd || ""
    draft.value = currentPwd.value
    errorMessage.value = `Failed to load working dir: ${e.message || e}`
  }
}

watch(
  agentId,
  (id) => {
    recent.value = loadRecent(id)
    loadCurrent()
  },
  { immediate: true },
)

async function apply() {
  if (!agentId.value || !draft.value) return
  saving.value = true
  status.value = ""
  errorMessage.value = ""
  try {
    const res = await fetch(`/api/agents/${encodeURIComponent(agentId.value)}/working-dir`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: draft.value }),
    })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      errorMessage.value = body?.detail || `HTTP ${res.status}`
      return
    }
    currentPwd.value = body?.pwd || draft.value
    draft.value = currentPwd.value
    pushRecent(currentPwd.value)
    status.value = `Switched to ${currentPwd.value}`
  } catch (e) {
    errorMessage.value = e.message || String(e)
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
