import pluginVue from "eslint-plugin-vue"
import vuePrettier from "@vue/eslint-config-prettier"

export default [
  ...pluginVue.configs["flat/recommended"],
  vuePrettier,
  {
    rules: {
      // Allow single-word component names (our panels are named e.g. ChatPanel)
      "vue/multi-word-component-names": "off",
      // Don't require explicit emit declarations in <script setup>
      "vue/require-explicit-emits": "off",
      // Relax prop types requirement
      "vue/require-prop-types": "off",
      // Allow v-html (we use it carefully)
      "vue/no-v-html": "warn",
    },
  },
]
