/**
 * Notifications store — toasts and notification-center history.
 *
 * - push({level, title, body, actions}) adds one. Auto-dismisses
 *   after timeoutMs (default 6s). Level: info | warn | error | ok.
 * - dismiss(id) removes a toast (but keeps it in history).
 */

import { defineStore } from "pinia"
import { ref } from "vue"

const DEFAULT_TIMEOUT = 6000
const HISTORY_LIMIT = 200

export const useNotificationsStore = defineStore("notifications", () => {
  const toasts = ref(/** @type {Array<object>} */ ([]))
  const history = ref(/** @type {Array<object>} */ ([]))

  let nextId = 1

  function push({
    level = "info",
    title = "",
    body = "",
    actions = [],
    timeoutMs = DEFAULT_TIMEOUT,
  } = {}) {
    const id = `n${nextId++}`
    const entry = {
      id,
      level,
      title,
      body,
      actions,
      ts: new Date().toISOString(),
    }
    toasts.value = [...toasts.value, entry]
    history.value = [entry, ...history.value].slice(0, HISTORY_LIMIT)
    if (timeoutMs > 0) {
      setTimeout(() => dismiss(id), timeoutMs)
    }
    return id
  }

  function dismiss(id) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function clearHistory() {
    history.value = []
  }

  return { toasts, history, push, dismiss, clearHistory }
})
