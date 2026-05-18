<template>
  <div class="updates-panel space-y-5">
    <!-- ── Active install summary ─────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Active install</h3>
      <div class="text-[13px] space-y-1">
        <div>
          Version: <span class="font-mono">{{ state.active?.version || "—" }}</span>
          <span v-if="state.active?.build_id" class="ml-2 text-warm-500">(build {{ state.active.build_id }})</span>
        </div>
        <div v-if="state.active?.installed_at">
          Installed: <span class="font-mono">{{ formatDate(state.active.installed_at) }}</span>
        </div>
        <div>
          Platform: <span class="font-mono">{{ state.platform || "—" }}</span>
          · ABI: <span class="font-mono">{{ state.py_abi || "—" }}</span>
        </div>
        <div v-if="!state.launcher_install" class="text-warm-500">
          Running outside the launcher — update controls are disabled.
        </div>
        <div v-if="state.last_check_error" class="text-red-500 text-[12px]">
          Last probe: {{ state.last_check_error }}
        </div>
      </div>
      <div class="flex items-center gap-2 mt-3 flex-wrap">
        <button class="text-[12px] px-3 py-1 border rounded hover:bg-warm-100 dark:hover:bg-warm-800" :disabled="busy" @click="onCheckNow">Check now</button>
        <button class="text-[12px] px-3 py-1 border rounded bg-iolite text-white hover:bg-iolite-dark disabled:opacity-50" :disabled="!canUpdate" @click="onUpdate">{{ updateButtonLabel }}</button>
        <button class="text-[12px] px-3 py-1 border rounded hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50" :disabled="!canRollback" @click="onRollback">{{ rollbackLabel }}</button>
      </div>
      <div v-if="error" class="mt-3 text-[12px] text-red-500">{{ error }}</div>
    </section>

    <!-- ── Channel ────────────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Channel</h3>
      <div class="space-y-2 text-[13px]">
        <label v-for="opt in channelOptions" :key="opt.value" class="flex items-start gap-3">
          <input v-model="form.channel" type="radio" :value="opt.value" @change="onFormChange" />
          <div class="flex-1">
            <div class="font-medium">{{ opt.label }}</div>
            <div class="text-warm-500 text-[12px]">{{ opt.description }}</div>
            <div v-if="probeByChannel[opt.value]" class="text-[12px] mt-1">
              Latest: <span class="font-mono">{{ probeByChannel[opt.value].latest_version || "—" }}</span>
            </div>
          </div>
        </label>
      </div>
    </section>

    <!-- ── Feed source ────────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Release feed</h3>
      <div class="space-y-3 text-[13px]">
        <label class="flex items-center gap-3">
          <input v-model="form.feedKind" type="radio" value="github_releases" @change="onFormChange" />
          <span>GitHub Releases</span>
          <input v-if="form.feedKind === 'github_releases'" v-model="form.feedRepo" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" placeholder="Kohaku-Lab/KohakuTerrarium" @blur="onFormChange" />
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.feedKind" type="radio" value="custom" @change="onFormChange" />
          <span>Custom mirror</span>
          <input v-if="form.feedKind === 'custom'" v-model="form.feedUrl" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" placeholder="https://my.mirror/kt" @blur="onFormChange" />
        </label>
        <p v-if="form.feedKind === 'custom'" class="text-[12px] text-warm-500">
          Manifest path: <span class="font-mono">{{ form.feedUrl || "https://&lt;your-url&gt;" }}/{{ form.channel }}.json</span>
        </p>
      </div>
    </section>

    <!-- ── Pinned version ─────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Pinned version</h3>
      <div class="space-y-2 text-[13px]">
        <div class="flex items-center gap-3">
          <select v-model="form.pinned" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" @change="onFormChange">
            <option :value="null">No pin — follow channel latest</option>
            <option v-for="rel in availableReleases" :key="rel.version" :value="rel.version">
              {{ rel.version }}<span v-if="rel.build_id"> · {{ rel.build_id }}</span>
            </option>
          </select>
          <button v-if="form.pinned" class="text-[12px] px-2 py-1 border rounded" @click="clearPin">Clear</button>
        </div>
        <p class="text-warm-500 text-[12px]">
          Pin to stay on a specific version regardless of channel updates.
        </p>
      </div>
    </section>

    <!-- ── Update mode ────────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Update mode</h3>
      <div class="space-y-2 text-[13px]">
        <label class="flex items-center gap-3">
          <input v-model="form.updateMode" type="radio" value="manual" @change="onFormChange" />
          <span>Manual — never check</span>
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.updateMode" type="radio" value="notify-on-launch" @change="onFormChange" />
          <span>Notify on launch — check daily, prompt me</span>
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.updateMode" type="radio" value="auto-on-launch" @change="onFormChange" />
          <span>Auto on launch — check and install on launch</span>
        </label>
      </div>
    </section>

    <!-- ── Installed history ──────────────────────────────────────── -->
    <section v-if="state.installed?.length" class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Installed versions on disk</h3>
      <ul class="text-[13px] space-y-1">
        <li v-for="v in state.installed" :key="v.version" class="font-mono flex items-center gap-2">
          <span>{{ v.version }}</span>
          <span v-if="v.version === state.active?.version" class="text-iolite">[active]</span>
          <span v-else class="text-warm-500 text-[11px]">{{ formatDate(v.installed_at) }}</span>
        </li>
      </ul>
    </section>

    <!-- ── Progress modal ─────────────────────────────────────────── -->
    <div v-if="progress.open" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div class="bg-warm-50 dark:bg-warm-900 rounded shadow-lg p-5 w-[420px]">
        <h3 class="text-sm font-semibold mb-2">{{ progress.phase || "Working…" }}</h3>
        <div class="h-2 bg-warm-200 dark:bg-warm-800 rounded overflow-hidden">
          <div class="h-full bg-iolite transition-all" :style="{ width: Math.max(0, Math.min(100, progress.percent || 0)) + '%' }" />
        </div>
        <p class="font-mono text-[11px] mt-3 break-words min-h-[14px] opacity-75">
          {{ progress.message }}
        </p>
        <p v-if="progress.status === 'ok' && progress.restartRequired" class="text-[12px] mt-3 text-iolite">Quit and relaunch the app to use the new version.</p>
        <div class="mt-3 flex justify-end gap-2">
          <button class="text-[12px] px-3 py-1 border rounded" @click="closeProgress">
            {{ progress.status ? "Close" : "Cancel" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue"
import { ElMessage } from "element-plus"

import { appUpdateAPI } from "@/utils/appUpdateApi"

const channelOptions = [
  { value: "stable", label: "Stable", description: "Tested releases. Recommended." },
  { value: "beta", label: "Beta", description: "Pre-release candidates." },
  { value: "nightly", label: "Nightly", description: "Daily automatic builds. Cutting edge." },
]

const form = reactive({
  channel: "stable",
  feedKind: "github_releases",
  feedRepo: "Kohaku-Lab/KohakuTerrarium",
  feedUrl: "",
  pinned: null,
  updateMode: "notify-on-launch",
})

const state = reactive({
  active: null,
  installed: [],
  launcher_install: false,
  legacy_bundle: false,
  platform: null,
  py_abi: null,
  last_check_at: null,
  last_check_error: null,
})

const probeByChannel = reactive({})
const availableReleases = ref([])

const busy = ref(false)
const error = ref("")
const ws = ref(null)
const progress = reactive({
  open: false,
  phase: "",
  percent: 0,
  message: "",
  status: null,
  restartRequired: false,
})

const latestForChannel = computed(() => probeByChannel[form.channel]?.latest_version || null)

const canUpdate = computed(() => {
  if (busy.value || !state.launcher_install) return false
  const latest = latestForChannel.value
  if (!latest) return true // allow click; will probe if needed
  return form.pinned ? form.pinned !== state.active?.version : latest !== state.active?.version
})

const canRollback = computed(() => {
  if (busy.value || !state.launcher_install) return false
  const active = state.active?.version
  return (state.installed || []).some((v) => v.version !== active)
})

const updateButtonLabel = computed(() => {
  if (form.pinned) return `Install pinned ${form.pinned}`
  const latest = latestForChannel.value
  return latest ? `Update to ${latest}` : "Update"
})

const rollbackLabel = computed(() => {
  const active = state.active?.version
  const prev = (state.installed || []).find((v) => v.version !== active)
  return prev ? `Rollback to ${prev.version}` : "Rollback"
})

function formatDate(iso) {
  if (!iso) return "—"
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function applySettings(payload) {
  const feed = payload.feed || {}
  form.channel = payload.channel || "stable"
  form.feedKind = feed.kind || "github_releases"
  form.feedRepo = feed.repo || "Kohaku-Lab/KohakuTerrarium"
  form.feedUrl = feed.url || ""
  form.pinned = payload.pinned_version || null
  form.updateMode = (payload.update && payload.update.mode) || "notify-on-launch"
}

function applyState(payload) {
  state.active = payload.active || null
  state.installed = payload.installed || []
  state.launcher_install = !!payload.launcher_install
  state.legacy_bundle = !!payload.legacy_bundle
  state.platform = payload.platform || null
  state.py_abi = payload.py_abi || null
  state.last_check_at = payload.last_check_at || null
  state.last_check_error = payload.last_check_error || null
}

async function load() {
  try {
    const st = await appUpdateAPI.getState()
    applyState(st)
    applySettings(st.settings || {})
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "load failed"
  }
}

async function probeCurrentChannel() {
  try {
    const data = await appUpdateAPI.probeFeed()
    probeByChannel[data.channel] = data
    availableReleases.value = data.releases || []
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "probe failed"
  }
}

async function onFormChange() {
  if (form.feedKind === "custom" && !form.feedUrl) return
  try {
    busy.value = true
    error.value = ""
    const payload = {
      feed: {
        kind: form.feedKind,
        repo: form.feedRepo || "Kohaku-Lab/KohakuTerrarium",
        url: form.feedKind === "custom" ? form.feedUrl : null,
      },
      channel: form.channel,
      pinned_version: form.pinned || null,
      update: {
        mode: form.updateMode,
        "check-cache-hours": 24,
        "keep-versions": 3,
      },
    }
    const echoed = await appUpdateAPI.putSettings(payload)
    applySettings(echoed)
    await probeCurrentChannel()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "save failed"
  } finally {
    busy.value = false
  }
}

function clearPin() {
  form.pinned = null
  onFormChange()
}

async function onCheckNow() {
  try {
    busy.value = true
    error.value = ""
    await probeCurrentChannel()
    await load()
    ElMessage.success("Checked feed")
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "check failed"
  } finally {
    busy.value = false
  }
}

function openProgress() {
  progress.open = true
  progress.phase = "Starting…"
  progress.percent = 0
  progress.message = ""
  progress.status = null
  progress.restartRequired = false
}

function closeProgress() {
  progress.open = false
  if (ws.value) {
    try {
      ws.value.close()
    } catch {
      /* ignore */
    }
    ws.value = null
  }
}

async function onUpdate() {
  try {
    error.value = ""
    await appUpdateAPI.startUpdate()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "start failed"
    return
  }
  openProgress()
  ws.value = appUpdateAPI.openProgressStream({
    onFrame(frame) {
      if (frame.phase) progress.phase = frame.phase
      if (typeof frame.percent === "number") progress.percent = frame.percent
      if (frame.message !== undefined) progress.message = frame.message
      if (frame.status) {
        progress.status = frame.status
        progress.restartRequired = !!frame["restart-required"]
        if (frame.status === "ok") load()
      }
    },
    onClose() {
      ws.value = null
    },
  })
}

async function onRollback() {
  try {
    busy.value = true
    error.value = ""
    const result = await appUpdateAPI.rollback()
    if (!result.ok) {
      error.value = result.error || "rollback failed"
      return
    }
    ElMessage.success(`Reverted to ${result.version}. Restart the app to use it.`)
    await load()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "rollback failed"
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await load()
  if (state.launcher_install) await probeCurrentChannel()
})
</script>

<style scoped>
.updates-panel input[type="text"],
.updates-panel input[type="url"],
.updates-panel input[type="search"],
.updates-panel input:not([type]),
.updates-panel select {
  background-color: inherit;
  color: inherit;
}
</style>
