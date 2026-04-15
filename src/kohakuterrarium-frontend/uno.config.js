import {
  defineConfig,
  presetWind,
  presetAttributify,
  presetIcons,
  transformerDirectives,
} from "unocss"

export default defineConfig({
  presets: [
    presetWind(),
    presetAttributify(),
    presetIcons({
      scale: 1.2,
      extraProperties: {
        display: "inline-block",
        "vertical-align": "middle",
      },
    }),
  ],
  transformers: [transformerDirectives()],
  theme: {
    colors: {
      // Gem accent colors
      sapphire: {
        light: "#D6E3F8",
        DEFAULT: "#0F52BA",
        shadow: "#082567",
      },
      aquamarine: {
        light: "#D4EDE8",
        DEFAULT: "#4C9989",
        shadow: "#1B6B5A",
      },
      taaffeite: {
        light: "#E8D5ED",
        DEFAULT: "#A57EAE",
        shadow: "#6B4670",
      },
      iolite: {
        light: "#DDD0F0",
        DEFAULT: "#5A4FCF",
        shadow: "#312A7A",
      },
      amber: {
        light: "#F5E6C8",
        DEFAULT: "#D4920A",
        shadow: "#8B5E00",
      },
      // Functional
      coral: {
        light: "#F5D5D5",
        DEFAULT: "#D46B6B",
        shadow: "#8B3A3A",
      },
      sage: {
        light: "#D5E8DA",
        DEFAULT: "#5A9E6F",
        shadow: "#3A6B48",
      },
      // Surface (light mode)
      warm: {
        50: "#F7F5F2",
        100: "#EFECE7",
        200: "#E0DBD4",
        300: "#C5BFB7",
        400: "#A09A92",
        500: "#8A8480",
        600: "#6A645F",
        700: "#4A4540",
        800: "#3A3632",
        900: "#2A2724",
        950: "#1A1816",
      },
    },
  },
  shortcuts: {
    "nav-rail":
      "w-14 flex flex-col items-center py-3 gap-1 border-r border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-950",
    "nav-item":
      "w-10 h-10 flex items-center justify-center rounded-lg cursor-pointer bg-transparent text-warm-500 dark:text-warm-400 hover:bg-warm-200/60 dark:hover:bg-warm-700/40 transition-colors",
    "nav-item-active":
      "nav-item !bg-warm-200/80 dark:!bg-warm-800/60 !text-iolite dark:!text-iolite-light",
    card: "bg-white dark:bg-warm-900 rounded-xl border border-warm-200/60 dark:border-warm-700/60",
    "card-hover":
      "card hover:border-warm-300/80 dark:hover:border-warm-600/60 hover:shadow-sm transition-all cursor-pointer",
    "btn-primary":
      "px-4 py-2 rounded-lg bg-iolite text-white hover:bg-iolite-shadow transition-colors font-medium text-sm border-none",
    "btn-secondary":
      "px-4 py-2 rounded-lg bg-warm-100 dark:bg-warm-800 text-warm-700 dark:text-warm-300 hover:bg-warm-200 dark:hover:bg-warm-700 transition-colors font-medium text-sm border border-warm-200/50 dark:border-warm-700/50",
    "gem-badge": "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium",
    "container-page": "max-w-6xl mx-auto px-6 py-6",
    "section-title": "text-lg font-semibold text-warm-800 dark:text-warm-200 mb-4",
    "text-body": "text-sm text-warm-800 dark:text-warm-200",
    "text-secondary": "text-sm text-warm-500 dark:text-warm-400",
    "input-field":
      "w-full px-3 py-2 rounded-lg bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 text-warm-800 dark:text-warm-200 placeholder-warm-400 dark:placeholder-warm-600 focus:outline-none focus:border-iolite dark:focus:border-iolite-light transition-colors text-sm",
  },
})
