<template>
  <!--
    AppFrame: the kt.app renderer. Runs one HTML document in a sandboxed
    frame (scripts and forms only, opaque origin, CSP first in the srcdoc)
    and connects it to the widget host through the bridge in appBridge.js.
    Without a host (a user-pinned HTML block) the frame gets no bridge.
  -->
  <div class="app-frame" :class="{ 'app-frame-fill': fill }" data-test="app-frame">
    <div v-if="loadError" class="app-frame-notice text-coral" data-test="app-frame-load-error">Could not load the app: {{ loadError }}</div>
    <div v-else-if="srcdoc === null" class="app-frame-notice text-warm-400">Loading app…</div>
    <iframe v-else ref="frame" class="app-frame-iframe" :title="widget?.title || title || 'App'" sandbox="allow-scripts allow-forms" referrerpolicy="no-referrer" :srcdoc="srcdoc" :style="frameStyle" data-test="app-frame-iframe" @mouseenter="bridge.setHovering(true)" @mouseleave="bridge.setHovering(false)" @focus="bridge.noteInteraction()" />
    <div v-if="scriptError" class="app-frame-notice app-frame-error" data-test="app-frame-error">The app hit an error: {{ scriptError }}</div>
    <div v-for="(line, i) in sentLines" :key="i" class="text-[10px] text-warm-400 px-1" data-test="app-frame-sent">App sent: {{ line }}</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { ElMessageBox } from "element-plus"

import api from "@/utils/api"

import { createAppBridge } from "./appBridge.js"
import { buildSrcdoc, readTheme } from "./appDocument.js"

const CHAT_MAX_VH = 70
const MAX_SENT_LINES = 5

const props = defineProps({
  widget: { type: Object, default: null },
  host: { type: Object, default: null },
  html: { type: String, default: null },
  title: { type: String, default: "" },
  fill: { type: Boolean, default: false },
})

const frame = ref(null)
const srcdoc = ref(null)
const loadError = ref("")
const scriptError = ref("")
const sentLines = ref([])
const height = ref(240)
const theme = ref(readTheme())

const spec = computed(() => props.widget?.spec || {})
const fill = computed(() => props.fill || props.widget?.surface === "canvas")
const frameStyle = computed(() => (fill.value ? { height: "100%" } : { height: `min(${height.value}px, ${CHAT_MAX_VH}vh)` }))

function initPayload() {
  return {
    widgetId: props.widget?.id || "",
    title: props.widget?.title || props.title || "",
    data: spec.value.data ?? null,
    state: props.widget?.state || {},
    theme: theme.value,
    locale: globalThis.navigator?.language || "en",
  }
}

// Built once per document version; later data, state and theme changes go
// over the bridge so the running app keeps its own state.
function setDocument(html) {
  srcdoc.value = buildSrcdoc({
    html,
    hosts: props.host ? spec.value.allowed_hosts : [],
    init: initPayload(),
    theme: theme.value,
    bridge: Boolean(props.host),
  })
}

const bridge = createAppBridge({
  getFrame: () => frame.value,
  getWidget: () => props.widget,
  host: props.host,
  getInit: initPayload,
  onSent: (name) => {
    sentLines.value = [...sentLines.value, name].slice(-MAX_SENT_LINES)
  },
  onError: (message) => {
    scriptError.value = message
  },
  onResize: (h) => {
    height.value = Math.max(80, Math.ceil(h) + 4)
  },
  openLink: async (url) => {
    try {
      await ElMessageBox.confirm(url, "Open link from the app?", { confirmButtonText: "Open", cancelButtonText: "Cancel" })
      globalThis.open?.(url, "_blank", "noopener,noreferrer")
    } catch {
      /* dismissed */
    }
  },
})

async function loadDocument() {
  loadError.value = ""
  if (typeof props.html === "string") {
    setDocument(props.html)
    return
  }
  if (typeof spec.value.html === "string") {
    setDocument(spec.value.html)
    return
  }
  const url = spec.value.document_url
  if (typeof url !== "string" || !url.startsWith("/api/sessions/")) {
    loadError.value = "the app has no document"
    return
  }
  try {
    const resp = await api.get(url.slice(4), { responseType: "text", transformResponse: (r) => r })
    setDocument(String(resp.data ?? ""))
  } catch (err) {
    loadError.value = err?.message || String(err)
  }
}

function onMessage(event) {
  bridge.handleMessage(event)
}

let observer = null

onMounted(() => {
  globalThis.addEventListener?.("message", onMessage)
  globalThis.addEventListener?.("blur", bridge.onWindowBlur)
  if (globalThis.MutationObserver && globalThis.document?.documentElement) {
    observer = new MutationObserver(() => {
      theme.value = readTheme()
      bridge.notify("kt/theme", theme.value)
    })
    observer.observe(globalThis.document.documentElement, { attributes: true, attributeFilter: ["class"] })
  }
  loadDocument()
})

onBeforeUnmount(() => {
  globalThis.removeEventListener?.("message", onMessage)
  globalThis.removeEventListener?.("blur", bridge.onWindowBlur)
  observer?.disconnect()
})

watch(
  () => [spec.value.sha256, spec.value.document_url, spec.value.html, props.html],
  (next, prev) => {
    if (prev && next.some((v, i) => v !== prev[i])) {
      scriptError.value = ""
      loadDocument()
    }
  },
)

watch(
  () => JSON.stringify(spec.value.data ?? null),
  (next, prev) => {
    if (prev !== undefined && next !== prev) bridge.notify("kt/data", { data: spec.value.data ?? null })
  },
)

watch(
  () => JSON.stringify(props.widget?.state || {}),
  (next, prev) => {
    if (prev !== undefined && next !== prev) bridge.notify("kt/state", { state: props.widget?.state || {} })
  },
)

defineExpose({ bridge })
</script>

<style scoped>
.app-frame {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0.5rem 0;
}
.app-frame-fill {
  height: 100%;
  margin: 0;
}
.app-frame-iframe {
  width: 100%;
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.2));
  border-radius: 0.5rem;
  background: var(--color-bg, #fff);
}
.app-frame-fill .app-frame-iframe {
  flex: 1;
  border-radius: 0;
  border: 0;
}
.app-frame-notice {
  font-size: 0.75rem;
  padding: 0.5rem;
}
.app-frame-error {
  color: var(--el-color-danger, #c45656);
}
</style>
