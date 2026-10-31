<template>
  <div v-if="entry" class="kt-ext-fence" :data-renderer="id" data-test="ext-fence-block">
    <component :is="entry.component" />
  </div>
  <pre v-else class="kt-ext-fence-raw" data-test="ext-fence-raw">{{ source }}</pre>
</template>

<script setup>
import { computed, provide } from "vue"

import { messageRenderers } from "@/ext/messageRenderers"
import { MESSAGE_BLOCK_INJECT_KEY } from "@/ext/sdk/keys"

const props = defineProps({
  /** Renderer id the fence was named after. */
  id: { type: String, required: true },
  /** Text between the fence lines. */
  source: { type: String, default: "" },
})

const emit = defineEmits(["say"])

const entry = computed(() => messageRenderers.get(props.id) || null)

provide(MESSAGE_BLOCK_INJECT_KEY, {
  get id() {
    return props.id
  },
  source: computed(() => props.source),
  say: (text) => emit("say", text),
})
</script>

<style scoped>
.kt-ext-fence {
  min-width: 0;
  margin: 0.5rem 0;
}
.kt-ext-fence-raw {
  margin: 0.5rem 0;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
