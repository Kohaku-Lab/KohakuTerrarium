<template>
  <div class="kt-overlay" :class="{ 'kt-overlay--through': overlay.options.clickThrough }" :style="style" :data-overlay="overlay.key" data-test="overlay-frame">
    <div v-if="!overlay.options.clickThrough" class="kt-overlay__bar" :class="{ 'kt-overlay__bar--drag': overlay.options.draggable !== false }" data-test="overlay-bar" @pointerdown="beginDrag(overlay.key, $event, 'move')">
      <span class="kt-overlay__title">{{ overlay.label }}</span>
      <button type="button" class="kt-overlay__close" :aria-label="`Hide ${overlay.label}`" data-test="overlay-close" @pointerdown.stop @click="hideOverlay(overlay.key)">×</button>
    </div>
    <div class="kt-overlay__body">
      <component :is="overlay.component" />
    </div>
    <div v-if="overlay.options.resizable && !overlay.options.clickThrough" class="kt-overlay__resize" data-test="overlay-resize" @pointerdown="beginDrag(overlay.key, $event, 'resize')" />
  </div>
</template>

<script setup>
import { computed, provide } from "vue"

import { beginDrag, hideOverlay, overlayBounds } from "@/ext/overlays"
import { OVERLAY_INJECT_KEY } from "@/ext/sdk/keys"

const props = defineProps({
  overlay: { type: Object, required: true },
})

provide(OVERLAY_INJECT_KEY, props.overlay.key)

const style = computed(() => {
  const box = overlayBounds(props.overlay.key)
  return {
    left: `${box.left}px`,
    top: `${box.top}px`,
    width: `${box.width}px`,
    height: `${box.height}px`,
  }
})
</script>

<style scoped>
.kt-overlay {
  position: absolute;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  pointer-events: auto;
  background: var(--color-surface, #fff);
  color: var(--color-text, inherit);
  border: 1px solid var(--color-border, rgba(0, 0, 0, 0.15));
  border-radius: 10px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.22);
}
.kt-overlay--through {
  pointer-events: none;
  background: transparent;
  border: 0;
  box-shadow: none;
}
.kt-overlay--through :deep(.kt-overlay-hit) {
  pointer-events: auto;
}
.kt-overlay__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 6px 4px 10px;
  font-size: 12px;
  user-select: none;
  border-bottom: 1px solid var(--color-border, rgba(0, 0, 0, 0.1));
}
.kt-overlay__bar--drag {
  cursor: grab;
  touch-action: none;
}
.kt-overlay__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kt-overlay__close {
  width: 22px;
  height: 22px;
  line-height: 1;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 4px;
  color: inherit;
}
.kt-overlay__close:hover {
  background: rgba(127, 127, 127, 0.2);
}
.kt-overlay__body {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.kt-overlay__resize {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  touch-action: none;
}
</style>
