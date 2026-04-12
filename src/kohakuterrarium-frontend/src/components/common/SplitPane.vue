<template>
  <div ref="container" class="split-pane" :class="horizontal ? 'split-pane--h' : 'split-pane--v'">
    <div class="split-pane__first" :style="firstStyle">
      <slot name="first" />
    </div>
    <div
      class="split-pane__splitter"
      :class="[
        horizontal ? 'split-pane__splitter--h' : 'split-pane__splitter--v',
        dragging ? 'split-pane__splitter--active' : '',
      ]"
      @pointerdown.prevent="onPointerDown"
    />
    <div class="split-pane__second" :style="secondStyle">
      <slot name="second" />
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  /** true = top/bottom split, false = left/right split */
  horizontal: { type: Boolean, default: false },
  /** Initial size of first pane in percent */
  initialSize: { type: Number, default: 50 },
  /** Minimum size in percent */
  minSize: { type: Number, default: 15 },
  /** localStorage key for persisting size (optional) */
  persistKey: { type: String, default: "" },
})

const _saved = props.persistKey
  ? parseFloat(localStorage.getItem(`split-${props.persistKey}`) || "0")
  : 0
const size = ref(_saved || props.initialSize)
const dragging = ref(false)
const container = ref(null)

const firstStyle = computed(() =>
  props.horizontal
    ? { height: `${size.value}%`, minHeight: 0 }
    : { width: `${size.value}%`, minWidth: 0 },
)

const secondStyle = computed(() =>
  props.horizontal
    ? { height: `${100 - size.value}%`, minHeight: 0 }
    : { width: `${100 - size.value}%`, minWidth: 0 },
)

function onPointerDown(e) {
  dragging.value = true
  e.target.setPointerCapture(e.pointerId)
  e.target.addEventListener("pointermove", onPointerMove)
  e.target.addEventListener("pointerup", onPointerUp)
  e.target.addEventListener("pointercancel", onPointerUp)
}

function onPointerMove(e) {
  if (!dragging.value || !container.value) return
  const rect = container.value.getBoundingClientRect()
  let pct
  if (props.horizontal) {
    pct = ((e.clientY - rect.top) / rect.height) * 100
  } else {
    pct = ((e.clientX - rect.left) / rect.width) * 100
  }
  size.value = Math.max(props.minSize, Math.min(100 - props.minSize, pct))
}

function onPointerUp(e) {
  dragging.value = false
  e.target.releasePointerCapture(e.pointerId)
  if (props.persistKey) {
    localStorage.setItem(`split-${props.persistKey}`, String(size.value))
  }
  e.target.removeEventListener("pointermove", onPointerMove)
  e.target.removeEventListener("pointerup", onPointerUp)
  e.target.removeEventListener("pointercancel", onPointerUp)
}
</script>

<style scoped>
.split-pane {
  display: flex;
  overflow: hidden;
  width: 100%;
  height: 100%;
}
.split-pane--v {
  flex-direction: row;
}
.split-pane--h {
  flex-direction: column;
}

.split-pane__first,
.split-pane__second {
  overflow: hidden;
}

.split-pane__splitter {
  flex-shrink: 0;
  background: var(--color-border);
  transition: background 0.15s ease;
  touch-action: none;
}
.split-pane__splitter:hover,
.split-pane__splitter--active {
  background: var(--color-text-muted);
}
.split-pane__splitter--v {
  width: 3px;
  cursor: col-resize;
}
.split-pane__splitter--h {
  height: 3px;
  cursor: row-resize;
}
</style>
