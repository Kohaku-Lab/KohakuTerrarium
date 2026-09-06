import { showNotification } from '../notifications.mjs'

export const ElMessage = (options) => showNotification(options)
for (const type of ['info', 'success', 'warning', 'error']) {
  ElMessage[type] = (options) => showNotification({ ...(typeof options === 'string' ? { message: options } : options), type })
}
