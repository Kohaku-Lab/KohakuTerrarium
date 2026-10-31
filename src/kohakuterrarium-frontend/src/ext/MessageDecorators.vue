<template>
  <div v-if="shown.length" class="kt-decorators" :data-position="position" data-test="message-decorators">
    <div v-for="item in shown" :key="item.key" class="kt-decorator" :style="{ maxHeight: `${item.maxHeight}px` }" :data-test="`message-decorator-${item.id}`">
      <component :is="item.component" />
    </div>
  </div>
</template>

<script setup>
import { computed, provide, readonly } from "vue"

import { decoratorsFor } from "@/ext/decorators"
import { MESSAGE_CONTEXT_INJECT_KEY } from "@/ext/sdk/keys"

const props = defineProps({
  /** `header` or `footer`. */
  position: { type: String, required: true },
  /** Read-only description of the message: `{ id, role, text, turn, tab }`. */
  message: { type: Object, required: true },
})

const shown = computed(() => decoratorsFor(props.position, props.message.role))

provide(
  MESSAGE_CONTEXT_INJECT_KEY,
  computed(() => readonly({ ...props.message })),
)
</script>

<style scoped>
.kt-decorators {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
  font-size: 12px;
}
.kt-decorator {
  min-width: 0;
  overflow: auto;
}
</style>
