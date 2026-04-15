import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const MIN_UI_ZOOM = 0.6
export const MAX_UI_ZOOM = 2
export const DEFAULT_DESKTOP_ZOOM = 1
export const DEFAULT_MOBILE_ZOOM = 1

function clampZoom(value, fallback) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.round(Math.max(MIN_UI_ZOOM, Math.min(MAX_UI_ZOOM, parsed)) * 100) / 100
}

export const useThemeStore = defineStore("theme", {
  state: () => ({
    dark: getHybridPrefSync("theme", "system") === "dark",
    desktopZoom: clampZoom(
      getHybridPrefSync("kt-desktop-zoom", DEFAULT_DESKTOP_ZOOM),
      DEFAULT_DESKTOP_ZOOM,
    ),
    mobileZoom: clampZoom(
      getHybridPrefSync("kt-mobile-zoom", DEFAULT_MOBILE_ZOOM),
      DEFAULT_MOBILE_ZOOM,
    ),
    _isMobile: false,
  }),

  getters: {
    uiZoom: (state) => (state._isMobile ? state.mobileZoom : state.desktopZoom),
  },

  actions: {
    toggle() {
      this.dark = !this.dark
      this.apply()
    },

    setMobileMode(isMobile) {
      this._isMobile = isMobile
      this.applyZoom()
    },

    setDesktopZoom(value) {
      this.desktopZoom = clampZoom(value, DEFAULT_DESKTOP_ZOOM)
      setHybridPref("kt-desktop-zoom", this.desktopZoom)
      this.applyZoom()
    },

    setMobileZoom(value) {
      this.mobileZoom = clampZoom(value, DEFAULT_MOBILE_ZOOM)
      setHybridPref("kt-mobile-zoom", this.mobileZoom)
      this.applyZoom()
    },

    setZoom(value) {
      if (this._isMobile) {
        this.setMobileZoom(value)
      } else {
        this.setDesktopZoom(value)
      }
    },

    applyZoom() {
      const el = document.getElementById("app")
      if (!el) return
      const z = clampZoom(this.uiZoom, this._isMobile ? DEFAULT_MOBILE_ZOOM : DEFAULT_DESKTOP_ZOOM)
      el.style.zoom = z === 1.0 ? "" : String(z)
    },

    apply() {
      document.documentElement.classList.toggle("dark", this.dark)
      setHybridPref("theme", this.dark ? "dark" : "light")
    },

    init() {
      const storedTheme = getHybridPrefSync("theme", "system")
      this.dark =
        storedTheme === "dark" ||
        (storedTheme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches)
      this.desktopZoom = clampZoom(
        getHybridPrefSync("kt-desktop-zoom", DEFAULT_DESKTOP_ZOOM),
        DEFAULT_DESKTOP_ZOOM,
      )
      this.mobileZoom = clampZoom(
        getHybridPrefSync("kt-mobile-zoom", DEFAULT_MOBILE_ZOOM),
        DEFAULT_MOBILE_ZOOM,
      )
      setHybridPref("kt-desktop-zoom", this.desktopZoom)
      setHybridPref("kt-mobile-zoom", this.mobileZoom)
      if (storedTheme !== "system") {
        setHybridPref("theme", this.dark ? "dark" : "light")
      }
      this.apply()
      this.applyZoom()
    },
  },
})
