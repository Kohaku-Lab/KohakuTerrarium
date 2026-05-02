import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const DEFAULT_LOCALE = "en"
export const SUPPORTED_LOCALES = ["en", "zh-TW", "zh-CN", "ja", "de", "ko"]
export const LOCALE_DISPLAY_NAMES = {
  en: "English",
  "zh-TW": "繁體中文",
  "zh-CN": "简体中文",
  ja: "日本語",
  de: "Deutsch",
  ko: "한국어",
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

  getters: {
    /** Convenience alias used by surfaces that don't want to know
     *  whether the field is called ``locale`` or ``current``. */
    current: (state) => state.locale,
    /** Human-readable display name for the current locale. */
    displayName: (state) => LOCALE_DISPLAY_NAMES[state.locale] ?? state.locale,
    /** Ordered list of supported locales — used by the cycle button
     *  AND by the Settings dropdown so they share the same source of
     *  truth. */
    supported: () => [...SUPPORTED_LOCALES],
  },

  actions: {
    setLocale(value) {
      this.locale = normalizeLocale(value)
      setHybridPref(LOCALE_PREF_KEY, this.locale)
      this.apply()
    },

    /** Rotate to the next supported locale. Invoked by the rail
     *  footer's tiny ``en``/``zh-TW``/... button. */
    cycle() {
      const idx = SUPPORTED_LOCALES.indexOf(this.locale)
      const next = SUPPORTED_LOCALES[(idx + 1) % SUPPORTED_LOCALES.length]
      this.setLocale(next)
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
