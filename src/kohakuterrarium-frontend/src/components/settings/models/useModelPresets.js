import { computed, ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import { cloneOf, groupPresets, presetKey } from "@/components/settings/models/presetModel"
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

  async function load() {
    try {
      const data = await configAPI.getModels()
      presets.value = Array.isArray(data) ? data : []
      const match =
        selectedKey.value && presets.value.find((p) => presetKey(p) === selectedKey.value)
      if (match) editing.value = match
    } catch {
      presets.value = []
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
      await settingsAPI.saveProfile(payload)
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
    startFrom,
    close,
    back,
    save,
    setDefault,
    remove,
  }
}
