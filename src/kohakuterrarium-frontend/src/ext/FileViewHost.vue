<template>
  <div class="h-full min-h-0 overflow-auto" :data-view="view.key" data-test="ext-file-view">
    <component :is="view.component" />
  </div>
</template>

<script setup>
import { computed, provide, readonly } from "vue"

import { FILE_VIEW_INJECT_KEY } from "@/ext/sdk/keys"

const props = defineProps({
  /** The registered file view to draw. */
  view: { type: Object, required: true },
  path: { type: String, required: true },
  /** Current buffer text. */
  content: { type: String, default: "" },
  language: { type: String, default: "" },
})

provide(
  FILE_VIEW_INJECT_KEY,
  computed(() =>
    readonly({
      path: props.path,
      name: props.path.split(/[\\/]/).pop(),
      content: props.content,
      language: props.language,
    }),
  ),
)
</script>
