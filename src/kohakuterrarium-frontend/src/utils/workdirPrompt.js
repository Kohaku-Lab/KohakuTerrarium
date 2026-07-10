// Post-resume missing-workdir recovery, shared by every resume entry
// point (sessions list, dashboard recent row, tab-store reopen). The
// sessions list additionally pre-prompts BEFORE resume when the list
// row already carries pwd_exists=false — this helper is the fallback
// for paths where that flag only arrives with the resume response.
export async function promptForMissingWorkdirAfterResume(result) {
  if (result?.session?.pwd_exists !== false) return
  // Lazy imports: this module sits on the dashboard/tab-store import
  // chains — a static element-plus pull here bloats their module-load
  // cost for a dialog that almost never opens.
  const { ElMessage, ElMessageBox } = await import("element-plus")
  const { useI18n } = await import("@/utils/i18n")
  const { t } = useI18n()
  const saved = result.session?.pwd || ""
  const creatures = (result.session?.creatures || []).filter((c) => c.creature_id)
  let value
  try {
    ;({ value } = await ElMessageBox.prompt(
      t("sessions.workdirMissingPrompt", { pwd: saved }),
      t("sessions.workdirMissingTitle"),
      { inputValue: "" },
    ))
  } catch {
    return // keep the server-side fallback dir
  }
  const dir = (value || "").trim()
  if (!dir || !creatures.length) return
  const { terrariumAPI } = await import("@/utils/api")
  try {
    for (const c of creatures) {
      await terrariumAPI.setWorkingDir(result.instance_id, c.creature_id, dir)
    }
    ElMessage.success(t("sessions.workdirSet", { path: dir }))
  } catch (err) {
    ElMessage.error(
      t("sessions.workdirSetFailed", { message: err.response?.data?.detail || err.message }),
    )
  }
}
