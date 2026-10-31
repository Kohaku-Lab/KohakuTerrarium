import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue"
import { ElMessage } from "element-plus"

import { appUpdateAPI } from "@/utils/appUpdateApi"
import { useI18n } from "@/utils/i18n"

export const UPDATE_CHANNELS = ["stable", "beta", "nightly"]
export const UPDATE_MODES = ["manual", "notify-on-launch", "auto-on-launch"]
const DEFAULT_REPO = "Kohaku-Lab/KohakuTerrarium"

function errorText(err, fallback) {
  return err?.response?.data?.detail || err?.message || fallback
}

/**
 * State and actions of the self-update surface: the active install, the update
 * settings form, the channel probe, the progress stream and rollback.
 */
export function useAppUpdate() {
  const { t } = useI18n()

  const form = reactive({
    channel: "stable",
    feedKind: "github_releases",
    feedRepo: DEFAULT_REPO,
    feedUrl: "",
    pinned: null,
    updateMode: "notify-on-launch",
  })
  const state = reactive({
    active: null,
    installed: [],
    launcher_install: false,
    platform: null,
    py_abi: null,
    last_check_at: null,
    last_check_error: null,
  })
  const probe = reactive({ latest: null, updateAvailable: null, releases: [], checkedAt: null })
  const busy = ref(false)
  const loaded = ref(false)
  const error = ref("")
  const progress = reactive({
    open: false,
    phase: "",
    percent: 0,
    message: "",
    status: null,
    restartRequired: false,
  })
  let stream = null

  const managed = computed(() => state.launcher_install)
  const target = computed(() => form.pinned || probe.latest)
  const previous = computed(
    () => (state.installed || []).find((item) => item.version !== state.active?.version) || null,
  )
  const canRollback = computed(() => managed.value && !busy.value && previous.value !== null)

  // unmanaged: outside the launcher; unknown: not probed yet.
  const status = computed(() => {
    if (!managed.value) return "unmanaged"
    if (probe.updateAvailable === null) return "unknown"
    return probe.updateAvailable ? "available" : "current"
  })
  const checkedAt = computed(() => probe.checkedAt || state.last_check_at || null)

  function applySettings(payload) {
    const feed = payload.feed || {}
    form.channel = payload.channel || "stable"
    form.feedKind = feed.kind || "github_releases"
    form.feedRepo = feed.repo || DEFAULT_REPO
    form.feedUrl = feed.url || ""
    form.pinned = payload.pinned_version || null
    form.updateMode = payload.update?.mode || "notify-on-launch"
  }

  function applyState(payload) {
    state.active = payload.active || null
    state.installed = payload.installed || []
    state.launcher_install = !!payload.launcher_install
    state.platform = payload.platform || null
    state.py_abi = payload.py_abi || null
    state.last_check_at = payload.last_check_at || null
    state.last_check_error = payload.last_check_error || null
  }

  async function load() {
    try {
      const payload = await appUpdateAPI.getState()
      applyState(payload)
      applySettings(payload.settings || {})
    } catch (err) {
      error.value = errorText(err, t("software.loadFailed"))
    } finally {
      loaded.value = true
    }
  }

  async function probeFeed() {
    const data = await appUpdateAPI.probeFeed()
    probe.latest = data.latest_version || null
    probe.updateAvailable = data.update_available ?? null
    probe.releases = data.releases || []
    probe.checkedAt = new Date().toISOString()
  }

  async function checkNow() {
    busy.value = true
    error.value = ""
    try {
      await probeFeed()
      await load()
    } catch (err) {
      error.value = errorText(err, t("software.checkFailed"))
    } finally {
      busy.value = false
    }
  }

  // Persist the form, then re-probe because the channel, feed or pin may have changed.
  function set(patch) {
    Object.assign(form, patch)
  }

  // Change form fields and persist them in one step.
  function edit(patch) {
    set(patch)
    return save()
  }

  async function save() {
    if (!managed.value || (form.feedKind === "custom" && !form.feedUrl)) return
    busy.value = true
    error.value = ""
    try {
      const echoed = await appUpdateAPI.putSettings({
        feed: {
          kind: form.feedKind,
          repo: form.feedRepo || DEFAULT_REPO,
          url: form.feedKind === "custom" ? form.feedUrl : null,
        },
        channel: form.channel,
        pinned_version: form.pinned || null,
        update: { mode: form.updateMode, "check-cache-hours": 24, "keep-versions": 3 },
      })
      applySettings(echoed)
      await probeFeed()
    } catch (err) {
      error.value = errorText(err, t("software.saveFailed"))
    } finally {
      busy.value = false
    }
  }

  function closeProgress() {
    progress.open = false
    stream?.close()
    stream = null
  }

  async function startUpdate() {
    error.value = ""
    try {
      await appUpdateAPI.startUpdate()
    } catch (err) {
      error.value = errorText(err, t("software.updateFailed"))
      return
    }
    Object.assign(progress, {
      open: true,
      phase: t("software.progress.starting"),
      percent: 0,
      message: "",
      status: null,
      restartRequired: false,
    })
    stream = appUpdateAPI.openProgressStream({
      onFrame(frame) {
        if (frame.phase) progress.phase = frame.phase
        if (typeof frame.percent === "number") progress.percent = frame.percent
        if (frame.message !== undefined) progress.message = frame.message
        if (!frame.status) return
        progress.status = frame.status
        progress.restartRequired = !!frame["restart-required"]
        if (frame.status === "ok")
          void load()
            .then(probeFeed)
            .catch(() => {})
      },
      onClose() {
        stream = null
      },
    })
  }

  async function rollBack() {
    busy.value = true
    error.value = ""
    try {
      const result = await appUpdateAPI.rollback()
      if (!result.ok) {
        error.value = result.error || t("software.rollbackFailed")
        return
      }
      ElMessage.success(t("software.rolledBack", { version: result.version }))
      await load()
    } catch (err) {
      error.value = errorText(err, t("software.rollbackFailed"))
    } finally {
      busy.value = false
    }
  }

  onMounted(async () => {
    await load()
    if (!managed.value) return
    try {
      await probeFeed()
    } catch (err) {
      error.value = errorText(err, t("software.checkFailed"))
    }
  })
  onBeforeUnmount(closeProgress)

  return {
    form,
    state,
    probe,
    busy,
    loaded,
    error,
    progress,
    managed,
    target,
    previous,
    canRollback,
    status,
    checkedAt,
    save,
    checkNow,
    set,
    edit,
    startUpdate,
    rollBack,
    closeProgress,
  }
}
