export const useThemeStore = defineStore("theme", {
  state: () => ({
    dark: localStorage.getItem("theme") === "dark",
  }),

  actions: {
    toggle() {
      this.dark = !this.dark
      this.apply()
    },

    apply() {
      document.documentElement.classList.toggle("dark", this.dark)
      localStorage.setItem("theme", this.dark ? "dark" : "light")
    },

    init() {
      // Default to dark if no preference saved
      if (!localStorage.getItem("theme")) {
        this.dark = window.matchMedia("(prefers-color-scheme: dark)").matches
      }
      this.apply()
    },
  },
})
