<template>
  <!--
    WidgetBlock: the chrome around one widget. Looks the renderer up in the
    widget registry, adds the local minimize toggle, catches renderer errors
    (shows the fallback text and the error), and shows the fallback when no
    renderer is registered. ``mode="chat"`` honours the widget surface: a
    canvas or side-panel widget leaves a stub in the chat, a modal widget
    opens a dialog. ``mode="inline"`` always draws the body in place.
  -->
  <div v-if="placement === 'stub'" class="widget-stub" data-test="widget-stub">
    <span :class="icon" class="text-sm" />
    <span class="text-xs font-medium truncate">{{ summary || widget.renderer }}</span>
    <span class="text-[10px] text-warm-400">{{ stubLabel }}</span>
    <el-button v-if="canReveal" size="small" link type="primary" data-test="widget-stub-open" @click="reveal">Open</el-button>
  </div>
  <div v-else class="ui-event-wrapper" :class="{ 'ui-event-collapsed-wrapper': collapsed, 'widget-closed': widget.closed && !isLegacy, 'widget-fill': fill }" :data-widget="widget.renderer">
    <button v-if="canMoveToCanvas" class="widget-to-canvas" title="Open in canvas" data-test="widget-open-in-canvas" @click="host.openInCanvas()">
      <span class="i-carbon-launch" />
    </button>
    <button class="ui-event-minimize" :title="collapsed ? 'Expand' : 'Minimize'" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
      <span :class="collapsed ? 'i-carbon-add' : 'i-carbon-subtract'" />
    </button>
    <div v-if="collapsed" class="ui-event-collapsed-summary">
      <span :class="icon" class="text-xs" />
      <span class="text-xs">{{ summary }}</span>
      <span v-if="pending" class="text-[10px] text-amber ml-2">pending</span>
    </div>
    <div v-show="!collapsed" class="ui-event-body">
      <div v-if="renderError" class="widget-notice widget-error" data-test="widget-error">
        <div v-if="widget.fallback" class="text-sm whitespace-pre-wrap">{{ widget.fallback }}</div>
        <div class="text-[11px] text-coral">This widget failed to render: {{ renderError }}</div>
      </div>
      <div v-else-if="!entry" class="widget-notice" data-test="widget-unsupported">
        <div class="text-sm whitespace-pre-wrap">{{ widget.fallback || `Unsupported widget (${widget.renderer})` }}</div>
      </div>
      <el-dialog v-else-if="placement === 'modal'" v-model="dialogOpen" :title="widget.title || summary" width="min(640px, 92vw)" append-to-body data-test="widget-modal">
        <component :is="entry.component" :widget="widget" :host="host" />
      </el-dialog>
      <component :is="entry.component" v-else :widget="widget" :host="host" />
      <div v-if="placement === 'modal' && !dialogOpen && entry && !renderError" class="widget-stub">
        <span class="text-xs">{{ widget.title || summary }}</span>
        <el-button size="small" link type="primary" data-test="widget-modal-open" @click="dialogOpen = true">Open</el-button>
      </div>
      <div v-if="widget.closed && !isLegacy" class="text-[10px] text-warm-400 px-1" data-test="widget-closed">closed</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onErrorCaptured, ref, watch } from "vue"
import { ElButton, ElDialog } from "element-plus"

import { effectiveSurface, findWidgetRenderer } from "../../../ext/widgets.js"
import { ensureBuiltinWidgetRenderers } from "./builtin.js"
import { provideWidget, useWidgetSurfaceOpener } from "./useWidget.js"
import { legacyKindOf } from "./widgetModel.js"

ensureBuiltinWidgetRenderers()

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
  mode: { type: String, default: "chat" },
})

const collapsed = ref(false)
const renderError = ref("")
const dialogOpen = ref(!props.widget.closed)
const opener = useWidgetSurfaceOpener()

const entry = computed(() => findWidgetRenderer(props.widget.renderer))
const isLegacy = computed(() => legacyKindOf(props.widget.renderer) !== null)
const surface = computed(() => effectiveSurface(props.widget.renderer, props.widget.surface))

const placement = computed(() => {
  if (props.mode === "inline" || !entry.value) return "inline"
  if (surface.value === "canvas" || surface.value === "side-panel") return "stub"
  if (surface.value === "modal") return "modal"
  return "inline"
})

const fill = computed(() => props.mode === "inline" && surface.value === "canvas")
const icon = computed(() => entry.value?.icon?.(props.widget) || "i-carbon-application text-warm-500")
const summary = computed(() => entry.value?.summary?.(props.widget) || props.widget.title || "")
const pending = computed(() => props.widget.awaiting === true && !props.widget.closed)
const stubLabel = computed(() => (surface.value === "canvas" ? "opened in canvas" : "shown in the side panel"))
const canMoveToCanvas = computed(() => props.mode === "chat" && placement.value === "inline" && !isLegacy.value && !props.widget.closed && (entry.value?.surfaces || []).includes("canvas") && typeof props.host?.openInCanvas === "function")
const canReveal = computed(() => typeof opener?.open === "function" && (opener.surfaces || []).includes(surface.value))

watch(
  () => props.widget.closed,
  (closed) => {
    if (closed) dialogOpen.value = false
  },
)
watch(
  () => props.widget.renderer,
  () => (renderError.value = ""),
)

provideWidget({
  get widget() {
    return props.widget
  },
  get host() {
    return props.host
  },
})

onErrorCaptured((err) => {
  renderError.value = err?.message || String(err)
  return false
})

function reveal() {
  opener?.open?.(surface.value, props.widget.id)
}
</script>

<style scoped>
.ui-event-wrapper {
  position: relative;
}
.widget-fill {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.widget-fill > .ui-event-body {
  flex: 1;
  min-height: 0;
}
.ui-event-collapsed-wrapper {
  margin: 0.25rem 0;
}
.ui-event-minimize {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  width: 1.25rem;
  height: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 0.25rem;
  color: var(--color-text-muted, rgba(160, 160, 160, 0.7));
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 0.75rem;
  z-index: 1;
  transition:
    background 0.15s,
    color 0.15s;
}
.widget-to-canvas {
  position: absolute;
  top: 0.5rem;
  right: 2rem;
  width: 1.25rem;
  height: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 0.25rem;
  color: var(--color-text-muted, rgba(160, 160, 160, 0.7));
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 0.75rem;
  z-index: 1;
}
.widget-to-canvas:hover,
.ui-event-minimize:hover {
  background: var(--color-card-hover, rgba(160, 160, 160, 0.12));
  color: var(--color-text, inherit);
}
.ui-event-collapsed-summary {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.625rem;
  border: 1px dashed var(--color-border, rgba(160, 160, 160, 0.2));
  border-radius: 0.375rem;
  color: var(--color-text-muted, rgba(160, 160, 160, 0.85));
  background: var(--color-card, rgba(160, 160, 160, 0.03));
}
.widget-closed .ui-event-body {
  opacity: 0.6;
}
.widget-notice {
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.18));
  border-radius: 0.5rem;
  padding: 0.625rem 0.75rem;
  margin: 0.5rem 0;
}
.widget-stub {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.625rem;
  margin: 0.25rem 0;
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.2));
  border-radius: 0.375rem;
}
</style>
