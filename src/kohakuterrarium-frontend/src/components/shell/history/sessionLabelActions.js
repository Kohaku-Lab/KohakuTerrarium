/**
 * Ask for and save a saved session's name or one-line summary, or have the
 * summary written again. Each resolves true when something was saved.
 */

import { ElMessage, ElMessageBox } from "element-plus"

import { sessionAPI } from "@/utils/api"

function failure(t, err) {
  ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  return false
}

async function ask(t, message, title, value) {
  try {
    const { value: text } = await ElMessageBox.prompt(message, title, {
      inputValue: value || "",
      confirmButtonText: t("common.save"),
      cancelButtonText: t("common.cancel"),
    })
    return text ?? ""
  } catch {
    return null
  }
}

export async function renameSession(t, key, current) {
  const text = await ask(t, t("lab.history.renamePrompt"), t("lab.history.rename"), current)
  if (text === null) return false
  try {
    await sessionAPI.setTitle(key, text)
    ElMessage.success(t("lab.history.saved"))
    return true
  } catch (err) {
    return failure(t, err)
  }
}

export async function editSummary(t, key, current) {
  const text = await ask(t, t("lab.history.summaryPrompt"), t("lab.history.editSummary"), current)
  if (text === null) return false
  try {
    await sessionAPI.setSummaryText(key, text)
    ElMessage.success(t("lab.history.saved"))
    return true
  } catch (err) {
    return failure(t, err)
  }
}

export async function regenerateSummary(t, key) {
  try {
    await sessionAPI.refreshSummary(key)
    return true
  } catch (err) {
    return failure(t, err)
  }
}
