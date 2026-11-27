import { computed, onScopeDispose, ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import {
  cloneOf,
  freeName,
  groupPresets,
  presetKey,
} from "@/components/settings/models/presetModel"
import { configAPI, settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/** State and actions of the Models section: the preset list, the open preset and its editor mode. */
export function useModelPresets() {
  const { t } = useI18n()

  const presets = ref([])
  const query = ref("")
  const filter = ref("all")
  const selectedKey = ref("")
  const mode = ref("new") // "new" | "edit" | "view"
  const editing = ref(null)
  const open = ref(false)

  const groups = computed(() => groupPresets(presets.value, query.value, filter.value))
  const shownCount = computed(() =>
    groups.value.reduce((sum, group) => sum + group.presets.length, 0),
  )

  let loadVersion = 0
  let disposed = false
  onScopeDispose(() => {
    disposed = true
    loadVersion++
  }, true)
  async function load() {
    if (disposed) return
    const version = ++loadVersion
    try {
      const data = await configAPI.getModels()
      if (version !== loadVersion) return
      if (!Array.isArray(data)) throw new Error(t("discovery.failed"))
      presets.value = data
      const match =
        selectedKey.value && presets.value.find((p) => presetKey(p) === selectedKey.value)
      if (match) editing.value = match
    } catch (err) {
      if (version === loadVersion)
        ElMessage.error(err?.response?.data?.detail || err?.message || t("discovery.failed"))
    }
  }

  function select(preset) {
    selectedKey.value = presetKey(preset)
    editing.value = preset
    mode.value = preset.source === "user" ? "edit" : "view"
    open.value = true
  }

  function startBlank() {
    selectedKey.value = ""
    editing.value = null
    mode.value = "new"
    open.value = true
  }

  function startFrom(base, provider) {
    if (!base) return
    selectedKey.value = ""
    editing.value = cloneOf(base, presets.value, provider)
    mode.value = "new"
    open.value = true
    ElMessage.info(t("settings.models.cloned", { name: base.name }))
  }

  async function openExisting(key) {
    const find = () => presets.value.find((p) => p.provider === key.provider && p.name === key.name)
    if (!find()) await load()
    const existing = find()
    if (existing) select(existing)
    else ElMessage.error(t("settings.models.noMatch"))
  }

  function startDiscovered(draft) {
    const sameProvider = presets.value.filter((p) => p.provider === draft.provider)
    const matches = sameProvider.filter((p) => p.model === draft.model && p.source === "user")
    const existing =
      sameProvider.find(
        (p) => p.name === draft.name && (p.source === "user" || p.model === draft.model),
      ) ||
      (matches.length === 1 && matches[0])
    if (existing) return select(existing)
    startBlank()
    const name = sameProvider.some((p) => p.name === draft.name)
      ? freeName(draft.name, draft.provider, presets.value)
      : draft.name
    editing.value = { ...draft, name }
  }

  function close() {
    open.value = false
    selectedKey.value = ""
    editing.value = null
  }

  /** Back to the list on a phone, keeping the row highlighted. */
  function back() {
    open.value = false
  }

  async function save(payload) {
    try {
      const createOnly = mode.value === "new" || presetKey(payload) !== selectedKey.value
      await settingsAPI.saveProfile(createOnly ? { ...payload, create_only: true } : payload)
      ElMessage.success(t("settings.models.saved", { name: payload.name }))
      selectedKey.value = `${payload.provider}/${payload.name}`
      await load()
      const saved = presets.value.find((p) => presetKey(p) === selectedKey.value)
      if (saved) select(saved)
    } catch (err) {
      ElMessage.error(err.response?.data?.detail || err.message || t("settings.models.saveFailed"))
    }
  }

  async function setDefault(preset) {
    if (!preset?.name) return
    // A bare name can exist under several providers.
    const identifier = preset.provider ? presetKey(preset) : preset.name
    try {
      const result = await settingsAPI.setDefaultModel(identifier)
      ElMessage.success(
        t("settings.models.defaultSet", { name: result?.default_model || identifier }),
      )
      await load()
    } catch (err) {
      ElMessage.error(err.response?.data?.detail || t("settings.models.defaultSetFailed"))
    }
  }

  async function remove(preset) {
    if (!preset?.provider) {
      ElMessage.error(t("settings.models.deleteFailed"))
      return
    }
    try {
      await ElMessageBox.confirm(t("settings.models.deleteConfirm"), {
        confirmButtonText: t("common.delete"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      })
    } catch {
      return
    }
    if (disposed) return
    try {
      await settingsAPI.deleteProfile(preset.name, preset.provider)
      ElMessage.success(t("settings.models.deleted", { name: preset.name }))
      if (presetKey(preset) === selectedKey.value) close()
      await load()
    } catch (err) {
      ElMessage.error(err.response?.data?.detail || t("settings.models.deleteFailed"))
    }
  }

  return {
    presets,
    query,
    filter,
    groups,
    shownCount,
    selectedKey,
    mode,
    editing,
    open,
    load,
    select,
    startBlank,
    openExisting,
    startDiscovered,
    startFrom,
    close,
    back,
    save,
    setDefault,
    remove,
  }
}
