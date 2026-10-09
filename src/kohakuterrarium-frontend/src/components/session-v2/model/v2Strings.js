/**
 * Strings of the v2 session shell, kept apart from the shared locale files
 * while v2 is a draft. Each area owns one dictionary file under
 * `strings/` ({en, "zh-TW", "zh-CN"}); this merges them. `useV2T()`
 * returns t(key, params) for the current app locale, falling back to
 * English, then to the key. `{name}` placeholders take params.
 */

import { useLocaleStore } from "@/stores/locale"

import add from "./strings/add"
import core from "./strings/core"
import debug from "./strings/debug"
import phone from "./strings/phone"
import settings from "./strings/settings"
import side from "./strings/side"
import status from "./strings/status"
import widgets from "./strings/widgets"
import workspace from "./strings/workspace"

const PARTS = [core, widgets, side, status, workspace, debug, settings, add, phone]
export const LOCALES = ["en", "zh-TW", "zh-CN"]

export const V2_STRINGS = Object.fromEntries(
  LOCALES.map((locale) => [locale, Object.assign({}, ...PARTS.map((p) => p[locale] || {}))]),
)

export function translate(locale, key, params = null) {
  const raw = V2_STRINGS[locale]?.[key] ?? V2_STRINGS.en[key] ?? key
  if (!params) return raw
  return raw.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m))
}

export function useV2T() {
  const locale = useLocaleStore()
  return (key, params) => translate(locale.locale, key, params)
}
