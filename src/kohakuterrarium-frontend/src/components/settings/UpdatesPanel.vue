<template>
  <div class="updates-panel space-y-5">
    <!-- ── Legacy-bundle migration banner ─────────────────────────── -->
    <div v-if="status.legacyBundle" class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/20 p-4 text-[13px]">
      <div class="font-medium mb-1">Switch to the new auto-updating bundle</div>
      <p class="opacity-80">You're running the legacy KohakuTerrarium Briefcase bundle. The new wrapper bundle updates the framework via pip, so you never have to re-download the installer again. Download once, switch over, and future updates land from this panel.</p>
      <a href="https://github.com/Kohaku-Lab/KohakuTerrarium/releases" target="_blank" rel="noopener" class="inline-block mt-2 text-iolite underline"> Download the wrapper bundle → </a>
    </div>

    <!-- ── Source ─────────────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Source</h3>
      <div class="space-y-2 text-[13px]">
        <label class="flex items-center gap-3">
          <input v-model="form.sourceKind" type="radio" value="pypi" @change="onFormChange" />
          <span>PyPI stable</span>
          <input v-if="form.sourceKind === 'pypi'" v-model="form.spec" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" placeholder="leave blank for latest, e.g. ==1.5.0 or <2.0" @blur="onFormChange" />
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.sourceKind" type="radio" value="git" @change="onFormChange" />
          <span>Git ref</span>
          <input v-if="form.sourceKind === 'git'" v-model="form.spec" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" placeholder="https://github.com/.../KohakuTerrarium.git@main" @blur="onFormChange" />
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.sourceKind" type="radio" value="local" @change="onFormChange" />
          <span>Local editable path</span>
          <input v-if="form.sourceKind === 'local'" v-model="form.spec" class="flex-1 px-2 py-1 border rounded text-[12px] dark:bg-warm-900" placeholder="/path/to/checkout" @blur="onFormChange" />
        </label>
        <label class="flex items-center gap-3">
          <input v-model="form.sourceKind" type="radio" value="bundled" @change="onFormChange" />
          <span>Bundled (offline)</span>
        </label>
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

    <!-- ── Status ─────────────────────────────────────────────────── -->
    <section class="rounded border border-warm-200 dark:border-warm-700 p-4">
      <h3 class="text-sm font-semibold mb-3">Status</h3>
      <div class="text-[13px] space-y-1">
        <div>
          Installed: <span class="font-mono">{{ status.currentVersion || "—" }}</span>
        </div>
        <div>
          Latest:
          <span class="font-mono">{{ status.latestVersion || "—" }}</span>
          <span v-if="status.available" class="ml-2 text-iolite">· update available</span>
        </div>
        <div>Last check: {{ formatLastCheck(status.lastCheckAt) }}</div>
        <div>
          Install kind:
          <span class="font-mono">{{ status.installKind || "—" }}</span>
          <span v-if="status.installKind !== 'wrapper'" class="ml-2 text-warm-500"> (wrapper-only features disabled) </span>
        </div>
      </div>
      <div class="flex items-center gap-2 mt-3">
        <button class="text-[12px] px-3 py-1 border rounded hover:bg-warm-100 dark:hover:bg-warm-800" :disabled="busy" @click="onCheckNow">Check now</button>
        <button class="text-[12px] px-3 py-1 border rounded bg-iolite text-white hover:bg-iolite-dark disabled:opacity-50" :disabled="busy || status.installKind !== 'wrapper' || !status.available" @click="onUpdate">Update</button>
        <button class="text-[12px] px-3 py-1 border rounded hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50" :disabled="busy || status.installKind !== 'wrapper'" @click="onRollback">Rollback</button>
      </div>
      <div v-if="error" class="mt-3 text-[12px] text-red-500">{{ error }}</div>
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
        <!--
          When the update succeeded and requires a restart, surface the
          instruction inline.  We don't expose a "Restart" button that
          quits + relaunches the app: that needs a dedicated runtime
          hook the framework doesn't yet provide.  Telling the user
          what to do beats faking an action that doesn't.
        -->
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
import { onMounted, reactive, ref } from "vue"
import { ElMessage } from "element-plus"

import { appUpdateAPI } from "@/utils/appUpdateApi"

const form = reactive({
  sourceKind: "pypi",
  spec: "",
  updateMode: "notify-on-launch",
})

const status = reactive({
  currentVersion: null,
  latestVersion: null,
  available: null,
  lastCheckAt: null,
  installKind: null,
  sourceKind: null,
  legacyBundle: false,
})

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

function applySettings(payload) {
  const src = payload.source || {}
  const upd = payload.update || {}
  form.sourceKind = src.kind || "pypi"
  form.spec = src.spec || ""
  form.updateMode = upd.mode || "notify-on-launch"
}

function applyStatus(payload) {
  status.currentVersion = payload["current-version"] || null
  status.latestVersion = payload["latest-version"] || null
  status.available = payload.available
  status.lastCheckAt = payload["last-check-at"] || null
  status.installKind = payload["install-kind"] || null
  status.sourceKind = payload["source-kind"] || null
  status.legacyBundle = !!payload["legacy-bundle"]
}

async function load() {
  try {
    const [s, st] = await Promise.all([appUpdateAPI.getSettings(), appUpdateAPI.getUpdateStatus()])
    applySettings(s)
    applyStatus(st)
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "load failed"
  }
}

async function onFormChange() {
  // Skip empty spec when kind requires one (UX nicety: don't 400 mid-typing).
  if ((form.sourceKind === "git" || form.sourceKind === "local") && !form.spec) {
    return
  }
  try {
    busy.value = true
    error.value = ""
    const patch = {
      source: {
        kind: form.sourceKind,
        spec: form.spec || null,
        extras: [],
      },
      update: { mode: form.updateMode, "check-cache-hours": 24 },
    }
    await appUpdateAPI.putSettings(patch)
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "save failed"
  } finally {
    busy.value = false
  }
}

async function onCheckNow() {
  try {
    busy.value = true
    error.value = ""
    const st = await appUpdateAPI.checkNow()
    applyStatus(st)
    if (!st.available) ElMessage.info("Already up-to-date")
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "check failed"
  } finally {
    busy.value = false
  }
}

function closeProgress() {
  if (ws.value) {
    try {
      ws.value.close()
    } catch {
      // Already closed.
    }
    ws.value = null
  }
  progress.open = false
}

async function onUpdate() {
  closeProgress()
  progress.open = true
  progress.phase = "Starting…"
  progress.percent = 0
  progress.message = ""
  progress.status = null
  progress.restartRequired = false
  try {
    await appUpdateAPI.startUpdate()
  } catch (e) {
    progress.status = "failed"
    progress.message = e?.response?.data?.detail || e.message || "update request failed"
    return
  }
  ws.value = appUpdateAPI.openProgressStream({
    onFrame: (frame) => {
      progress.phase = frame.phase || progress.phase
      progress.percent = frame.percent ?? progress.percent
      progress.message = frame.message || ""
      if (frame.status) {
        progress.status = frame.status
        progress.restartRequired = !!frame["restart-required"]
        if (frame.status === "ok") load()
      }
    },
    onClose: () => {
      ws.value = null
    },
  })
}

async function onRollback() {
  try {
    busy.value = true
    error.value = ""
    const r = await appUpdateAPI.rollback()
    if (!r.ok) {
      error.value = r.error || "rollback failed"
      return
    }
    ElMessage.success("Rolled back. Restart the app to use the previous version.")
    await load()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "rollback failed"
  } finally {
    busy.value = false
  }
}

function formatLastCheck(iso) {
  if (!iso) return "never"
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

onMounted(load)
</script>
