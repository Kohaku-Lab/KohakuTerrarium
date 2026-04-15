import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const DEFAULT_LOCALE = "en"
export const SUPPORTED_LOCALES = ["en", "zh-CN", "de"]
export const LOCALE_DISPLAY_NAMES = {
  en: "English",
  "zh-CN": "简体中文",
  de: "Deutsch",
}
const LOCALE_PREF_KEY = "kt-locale"

function normalizeLocale(value) {
  if (SUPPORTED_LOCALES.includes(value)) return value
  return DEFAULT_LOCALE
}

export const useLocaleStore = defineStore("locale", {
  state: () => ({
    locale: normalizeLocale(getHybridPrefSync(LOCALE_PREF_KEY, DEFAULT_LOCALE)),
  }),

  actions: {
    setLocale(value) {
      this.locale = normalizeLocale(value)
      setHybridPref(LOCALE_PREF_KEY, this.locale)
      this.apply()
    },

    apply() {
      if (typeof document !== "undefined") {
        document.documentElement.lang = this.locale
      }
    },

    init() {
      this.locale = normalizeLocale(getHybridPrefSync(LOCALE_PREF_KEY, DEFAULT_LOCALE))
      this.apply()
    },
  },
})
