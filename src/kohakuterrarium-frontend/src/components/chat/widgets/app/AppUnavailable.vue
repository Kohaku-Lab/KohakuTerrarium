<template>
  <!-- kt.app stand-in for a host that cannot run app frames: the title, a note and the source on request. -->
  <div class="app-unavailable" data-test="app-unavailable">
    <div class="app-unavailable-head">
      <span class="i-carbon-application-web" />
      <span class="app-unavailable-title">{{ appTitle }}</span>
    </div>
    <div class="app-unavailable-note">{{ t("app.unavailable") }}</div>
    <details v-if="source" class="app-unavailable-source">
      <summary>{{ t("app.unavailableSource") }}</summary>
      <pre><code>{{ source }}</code></pre>
    </details>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const props = defineProps({
  widget: { type: Object, default: null },
  html: { type: String, default: null },
  title: { type: String, default: "" },
})

const { t } = useI18n()

const appTitle = computed(() => props.title || props.widget?.title || "App")
const source = computed(() => {
  if (typeof props.html === "string") return props.html
  const html = props.widget?.spec?.html
  return typeof html === "string" ? html : ""
})
</script>

<style scoped>
.app-unavailable {
  border: 1px solid var(--color-border, rgba(127, 127, 127, 0.3));
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
}
.app-unavailable-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.app-unavailable-note {
  margin-top: 4px;
  opacity: 0.75;
}
.app-unavailable-source {
  margin-top: 6px;
}
.app-unavailable-source pre {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 4px 0 0;
}
</style>
