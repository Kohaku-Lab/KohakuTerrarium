<template>
  <!--
    AppFrame: the kt.app renderer. Runs one HTML document in a sandboxed
    frame (scripts and forms only, opaque origin, CSP first in the srcdoc)
    and connects it to the widget host through the bridge in appBridge.js.
    Without a host (a user-pinned HTML block) the frame gets no bridge;
    ``bridge`` forces one without an agent (inline ``<html>``: size, theme,
    viewport and links, but no ``kt.send`` or saved state).
    Full screen keeps the same iframe element, so the app never reloads.
  -->
  <div ref="shell" class="app-frame" :class="{ 'app-frame-fill': fill, 'app-frame-max': overlay, 'app-frame-native-full': nativeFull }" data-test="app-frame">
    <div class="app-frame-bar" :class="{ 'app-frame-bar-hosted': Boolean(host) && !maximized }" data-test="app-frame-bar">
      <span class="app-frame-title" :title="appTitle">{{ appTitle }}</span>
      <button type="button" class="app-frame-btn" :title="t('app.reload')" :disabled="srcdoc === null" data-test="app-frame-reload" @click="reload"><span class="i-carbon-renew" /></button>
      <button type="button" class="app-frame-btn" :title="t('app.source')" :disabled="sourceHtml === null" data-test="app-frame-source" @click="sourceOpen = true"><span class="i-carbon-code" /></button>
      <button type="button" class="app-frame-btn" :title="t('app.openTab')" :disabled="srcdoc === null" data-test="app-frame-tab" @click="openInTab"><span class="i-carbon-new-tab" /></button>
      <button v-if="!maximized" type="button" class="app-frame-btn" :title="t('app.fullscreen')" :disabled="srcdoc === null" data-test="app-frame-fullscreen" @click="enterFullscreen"><span class="i-carbon-maximize" /></button>
      <button v-else type="button" class="app-frame-btn app-frame-exit" :title="t('app.exitFullscreen')" data-test="app-frame-exit" @click="exitFullscreen"><span class="i-carbon-minimize" /> {{ t("app.exitFullscreen") }}</button>
    </div>
    <div ref="stage" class="app-frame-stage">
      <div v-if="loadError" class="app-frame-notice text-coral" data-test="app-frame-load-error">{{ t("app.loadError", { error: loadError }) }}</div>
      <div v-else-if="srcdoc === null" class="app-frame-notice text-warm-400">{{ t("app.loading") }}</div>
      <iframe v-else :key="frameKey" ref="frame" class="app-frame-iframe" :title="appTitle" sandbox="allow-scripts allow-forms" referrerpolicy="no-referrer" :srcdoc="srcdoc" :style="frameStyle" data-test="app-frame-iframe" @mouseenter="bridge.setHovering(true)" @mouseleave="bridge.setHovering(false)" @focus="bridge.noteInteraction()" />
      <div v-if="scriptError" class="app-frame-error" role="alert" data-test="app-frame-error">
        <span class="i-carbon-warning-alt shrink-0" />
        <span class="app-frame-error-text">{{ t("app.scriptError", { error: scriptError }) }}</span>
        <button type="button" class="app-frame-btn" :title="t('app.dismiss')" data-test="app-frame-error-dismiss" @click="scriptError = ''"><span class="i-carbon-close" /></button>
      </div>
    </div>
    <div v-for="(line, i) in sentLines" :key="i" class="text-[10px] text-warm-400 px-1" data-test="app-frame-sent">{{ t("app.sent", { name: line }) }}</div>
    <el-dialog v-model="sourceOpen" :title="t('app.sourceTitle', { title: appTitle })" width="min(860px, 94vw)" append-to-body data-test="app-frame-source-dialog">
      <pre class="app-frame-source">{{ sourceHtml }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { ElDialog, ElMessageBox } from "element-plus"

import api from "@/utils/api"
import { useI18n } from "@/utils/i18n"

import { createAppBridge } from "./appBridge.js"
import { buildSrcdoc, buildTabPage, readTheme } from "./appDocument.js"

const CHAT_MAX_VH = 70
const MAX_SENT_LINES = 5
const TAB_URL_LIFETIME_MS = 60_000

const props = defineProps({
  widget: { type: Object, default: null },
  host: { type: Object, default: null },
  html: { type: String, default: null },
  title: { type: String, default: "" },
  fill: { type: Boolean, default: false },
  bridge: { type: Boolean, default: null },
  fixedHeight: { type: Number, default: null },
})

const { t } = useI18n()

const shell = ref(null)
const stage = ref(null)
const frame = ref(null)
const srcdoc = ref(null)
const sourceHtml = ref(null)
const frameKey = ref(0)
const loadError = ref("")
const scriptError = ref("")
const sentLines = ref([])
const height = ref(240)
const theme = ref(readTheme())
const nativeFull = ref(false)
const overlay = ref(false)
const sourceOpen = ref(false)
const stageSize = ref({ width: 0, height: 0 })

const spec = computed(() => props.widget?.spec || {})
const appTitle = computed(() => props.widget?.title || props.title || "App")
const fill = computed(() => props.fill || props.widget?.surface === "canvas")
const maximized = computed(() => nativeFull.value || overlay.value)
const mode = computed(() => (maximized.value ? "fullscreen" : fill.value ? "canvas" : "chat"))
const frameStyle = computed(() => {
  if (fill.value || maximized.value) return { height: "100%" }
  return { height: `${props.fixedHeight ?? height.value}px`, maxHeight: `${CHAT_MAX_VH}vh` }
})
const withBridge = computed(() => props.bridge ?? Boolean(props.host))
const viewport = computed(() => ({ ...stageSize.value, mode: mode.value }))

function initPayload() {
  return {
    widgetId: props.widget?.id || "",
    title: appTitle.value,
    data: spec.value.data ?? null,
    state: props.widget?.state || {},
    theme: theme.value,
    viewport: viewport.value,
    locale: globalThis.navigator?.language || "en",
  }
}

function srcdocFor(html, init) {
  return buildSrcdoc({ html, hosts: props.host ? spec.value.allowed_hosts : [], init, theme: theme.value, bridge: withBridge.value })
}

// Built once per document version; later data, state, theme and viewport
// changes go over the bridge so the running app keeps its own state.
function setDocument(html) {
  sourceHtml.value = html
  srcdoc.value = srcdocFor(html, initPayload())
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
    height.value = Math.max(80, Math.ceil(h))
  },
  openLink: async (url) => {
    try {
      await ElMessageBox.confirm(url, t("app.openLinkTitle"), { confirmButtonText: t("app.open"), cancelButtonText: t("app.cancel") })
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

function reload() {
  if (sourceHtml.value === null) return
  scriptError.value = ""
  setDocument(sourceHtml.value)
  frameKey.value += 1
}

async function enterFullscreen() {
  const el = shell.value
  if (el?.requestFullscreen && globalThis.document?.fullscreenEnabled !== false) {
    try {
      await el.requestFullscreen()
      return
    } catch {
      /* the overlay below takes over */
    }
  }
  overlay.value = true
}

async function exitFullscreen() {
  overlay.value = false
  if (nativeFull.value && globalThis.document?.exitFullscreen) {
    try {
      await globalThis.document.exitFullscreen()
    } catch {
      nativeFull.value = false
    }
  }
}

function onFullscreenChange() {
  nativeFull.value = Boolean(shell.value) && globalThis.document?.fullscreenElement === shell.value
}

function onKeydown(event) {
  if (overlay.value && event.key === "Escape") exitFullscreen()
}

function openInTab() {
  if (sourceHtml.value === null) return
  const init = { ...initPayload(), viewport: { width: 0, height: 0, mode: "tab" } }
  const page = buildTabPage({ srcdoc: srcdocFor(sourceHtml.value, init), title: appTitle.value, init })
  const url = URL.createObjectURL(new Blob([page], { type: "text/html" }))
  globalThis.open?.(url, "_blank", "noopener")
  setTimeout(() => URL.revokeObjectURL(url), TAB_URL_LIFETIME_MS)
}

function onMessage(event) {
  bridge.handleMessage(event)
}

let themeObserver = null
let sizeObserver = null

onMounted(() => {
  globalThis.addEventListener?.("message", onMessage)
  globalThis.addEventListener?.("blur", bridge.onWindowBlur)
  globalThis.addEventListener?.("keydown", onKeydown)
  globalThis.document?.addEventListener?.("fullscreenchange", onFullscreenChange)
  if (globalThis.MutationObserver && globalThis.document?.documentElement) {
    themeObserver = new MutationObserver(() => {
      theme.value = readTheme()
      bridge.notify("kt/theme", theme.value)
    })
    themeObserver.observe(globalThis.document.documentElement, { attributes: true, attributeFilter: ["class"] })
  }
  if (globalThis.ResizeObserver && stage.value) {
    sizeObserver = new ResizeObserver(([entry]) => {
      const box = entry?.contentRect
      if (box) stageSize.value = { width: Math.round(box.width), height: Math.round(box.height) }
    })
    sizeObserver.observe(stage.value)
  }
  loadDocument()
})

onBeforeUnmount(() => {
  globalThis.removeEventListener?.("message", onMessage)
  globalThis.removeEventListener?.("blur", bridge.onWindowBlur)
  globalThis.removeEventListener?.("keydown", onKeydown)
  globalThis.document?.removeEventListener?.("fullscreenchange", onFullscreenChange)
  themeObserver?.disconnect()
  sizeObserver?.disconnect()
  if (nativeFull.value) globalThis.document?.exitFullscreen?.().catch?.(() => {})
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

watch(viewport, (next, prev) => {
  if (JSON.stringify(next) !== JSON.stringify(prev)) bridge.notify("kt/viewport", next)
})

defineExpose({ bridge, enterFullscreen, exitFullscreen })
</script>

<style scoped>
.app-frame {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0.5rem 0;
  background: var(--color-bg, #fff);
}
.app-frame-fill {
  height: 100%;
  margin: 0;
  gap: 0;
}
.app-frame-max {
  position: fixed;
  inset: 0;
  z-index: 3000;
  margin: 0;
  gap: 0;
}
.app-frame-native-full {
  width: 100vw;
  height: 100vh;
  margin: 0;
  gap: 0;
}
.app-frame-bar {
  display: flex;
  align-items: center;
  gap: 0.125rem;
  min-height: 1.75rem;
  padding: 0 0.25rem;
  font-size: 0.75rem;
  color: var(--color-text-muted, rgba(120, 120, 120, 0.9));
}
.app-frame-bar-hosted {
  padding-right: 3.75rem;
}
.app-frame-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.app-frame-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  height: 1.5rem;
  min-width: 1.5rem;
  justify-content: center;
  padding: 0 0.25rem;
  border: 0;
  border-radius: 0.25rem;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.app-frame-btn:hover:not(:disabled) {
  background: var(--color-card-hover, rgba(160, 160, 160, 0.14));
  color: var(--color-text, inherit);
}
.app-frame-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.app-frame-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.app-frame-fill .app-frame-stage,
.app-frame-max .app-frame-stage,
.app-frame-native-full .app-frame-stage {
  flex: 1;
}
.app-frame-iframe {
  width: 100%;
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.2));
  border-radius: 0.5rem;
  background: var(--color-bg, #fff);
}
.app-frame-fill .app-frame-iframe,
.app-frame-max .app-frame-iframe,
.app-frame-native-full .app-frame-iframe {
  flex: 1;
  border-radius: 0;
  border: 0;
}
.app-frame-notice {
  font-size: 0.75rem;
  padding: 0.5rem;
}
.app-frame-error {
  position: absolute;
  left: 0.5rem;
  right: 0.5rem;
  bottom: 0.5rem;
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.375rem 0.5rem;
  border-radius: 0.375rem;
  font-size: 0.75rem;
  color: #fff;
  background: rgba(176, 58, 46, 0.92);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}
.app-frame-error-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.app-frame-source {
  max-height: 70vh;
  overflow: auto;
  margin: 0;
  font-size: 0.75rem;
  font-family: var(--font-mono, ui-monospace, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
