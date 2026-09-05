import { computed } from "vue"

import { useLocaleStore } from "@/stores/locale"
import { messages } from "@/utils/i18n/locales"

const panelLabelKeys = {
  chat: "layout.panel.chat",
  "status-dashboard": "layout.panel.overview",
  "file-tree": "layout.panel.fileTree",
  "monaco-editor": "layout.panel.editor",
  "editor-status": "layout.panel.editorStatus",
  files: "layout.panel.files",
  activity: "layout.panel.jobs",
  settings: "layout.panel.settings",
  state: "layout.panel.creatureState",
  creatures: "layout.panel.creatures",
  canvas: "layout.panel.canvas",
  debug: "layout.panel.debug",
  terminal: "layout.panel.terminal",
  "status-tab": "layout.panel.statusDashboard",
  modules: "layout.panel.modules",
  drives: "layout.panel.drives",
}

const panelDescriptionKeys = {
  chat: "layout.panelDesc.chat",
  "status-dashboard": "layout.panelDesc.overview",
  "status-tab": "layout.panelDesc.status",
  "monaco-editor": "layout.panelDesc.editor",
  files: "layout.panelDesc.files",
  activity: "layout.panelDesc.jobs",
  settings: "layout.panelDesc.settings",
  state: "layout.panelDesc.creatureState",
  creatures: "layout.panelDesc.creatures",
  canvas: "layout.panelDesc.canvas",
  debug: "layout.panelDesc.debug",
  terminal: "layout.panelDesc.terminal",
  modules: "layout.panelDesc.modules",
  drives: "layout.panelDesc.drives",
}

const presetLabelKeys = {
  "chat-focus": "layout.preset.chatFocus",
  workspace: "layout.preset.workspace",
  "multi-creature": "layout.preset.multiCreature",
  canvas: "layout.preset.canvas",
  debug: "layout.preset.debug",
  settings: "layout.preset.settings",
  "chat-terminal": "layout.preset.chatTerminal",
}

function interpolate(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`))
}

function resolveMessage(locale, key) {
  const table = messages[locale] || messages.en
  return table[key] ?? messages.en[key]
}

export function translate(locale, key, params = {}) {
  const message = resolveMessage(locale, key)
  if (message == null) return key
  if (typeof message === "function") return message(params)
  return interpolate(message, params)
}

export function translatePanelLabel(locale, panelId, fallback = panelId) {
  const key = panelLabelKeys[panelId]
  if (!key) return fallback
  const message = resolveMessage(locale, key)
  if (message == null) return fallback
  return typeof message === "function" ? message({ panelId }) : message
}

export function translatePanelDescription(locale, panelId, fallback = "") {
  const key = panelDescriptionKeys[panelId]
  if (!key) return fallback
  const message = resolveMessage(locale, key)
  if (message == null) return fallback
  return typeof message === "function" ? message({ panelId }) : message
}

export function translatePresetLabel(locale, presetId, fallback = presetId) {
  const key = presetLabelKeys[presetId]
  if (!key) return fallback
  const message = resolveMessage(locale, key)
  if (message == null) return fallback
  return typeof message === "function" ? message({ presetId }) : message
}

export function translateStatusValue(locale, value, fallback = value) {
  const key = `status.value.${value}`
  const message = resolveMessage(locale, key)
  if (message == null) return fallback
  return typeof message === "function" ? message({ value }) : message
}

export function useI18n() {
  const localeStore = useLocaleStore()

  const locale = computed(() => localeStore.locale)

  function t(key, params = {}) {
    return translate(localeStore.locale, key, params)
  }

  function panelLabel(panelId, fallback = panelId) {
    return translatePanelLabel(localeStore.locale, panelId, fallback)
  }

  function panelDescription(panelId, fallback = "") {
    return translatePanelDescription(localeStore.locale, panelId, fallback)
  }

  function presetLabel(presetId, fallback = presetId) {
    return translatePresetLabel(localeStore.locale, presetId, fallback)
  }

  function statusLabel(value, fallback = value) {
    return translateStatusValue(localeStore.locale, value, fallback)
  }

  return {
    locale,
    t,
    panelLabel,
    panelDescription,
    presetLabel,
    statusLabel,
    localeStore,
  }
}
