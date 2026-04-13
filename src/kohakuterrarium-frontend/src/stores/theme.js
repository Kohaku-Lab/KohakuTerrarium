export const useThemeStore = defineStore("theme", {
  state: () => ({
    dark: localStorage.getItem("theme") === "dark",
    desktopZoom: parseFloat(localStorage.getItem("kt-desktop-zoom")) || 1.15,
    mobileZoom: parseFloat(localStorage.getItem("kt-mobile-zoom")) || 1.25,
    _isMobile: false,
  }),

  getters: {
    /** The active zoom level based on current layout mode. */
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
      this.desktopZoom = Math.round(Math.max(0.5, Math.min(2.0, value)) * 100) / 100
      localStorage.setItem("kt-desktop-zoom", String(this.desktopZoom))
      this.applyZoom()
    },

    setMobileZoom(value) {
      this.mobileZoom = Math.round(Math.max(0.5, Math.min(2.0, value)) * 100) / 100
      localStorage.setItem("kt-mobile-zoom", String(this.mobileZoom))
      this.applyZoom()
    },

    /** Convenience — sets whichever zoom is currently active. */
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
      const z = this.uiZoom
      el.style.zoom = z === 1.0 ? "" : String(z)
    },

    apply() {
      document.documentElement.classList.toggle("dark", this.dark)
      localStorage.setItem("theme", this.dark ? "dark" : "light")
    },

    init() {
      if (!localStorage.getItem("theme")) {
        this.dark = window.matchMedia("(prefers-color-scheme: dark)").matches
      }
      this.apply()
      this.applyZoom()
    },
  },
})
