<template>
  <!--
    kt.app for a host that runs apps outside the page (a VS Code editor tab):
    the title, an open button and the source. The host's ``openApp`` seam
    carries the same sandboxed document and the same bridge as AppFrame.
  -->
  <AppUnavailable v-if="!opener" :widget="widget" :html="html" :title="title" />
  <div v-else class="app-launcher" data-test="app-launcher">
    <div class="app-launcher-head">
      <span class="i-carbon-application-web" />
      <span class="app-launcher-title">{{ appTitle }}</span>
      <el-button size="small" type="primary" :loading="opening" data-test="app-launcher-open" @click="open">{{ isOpen ? t("app.reveal") : t("app.openApp") }}</el-button>
    </div>
    <div class="app-launcher-note">{{ t("app.launcherNote") }}</div>
    <div v-if="error" class="app-launcher-error" data-test="app-launcher-error">{{ error }}</div>
    <div v-for="(name, i) in sentLines" :key="i" class="app-launcher-note">{{ t("app.sent", { name }) }}</div>
    <details v-if="sourceHtml" class="app-launcher-source">
      <summary>{{ t("app.unavailableSource") }}</summary>
      <pre><code>{{ sourceHtml }}</code></pre>
    </details>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElButton } from "element-plus"

import { useI18n } from "@/utils/i18n"

import { useMessageActions } from "../../../../public/chat/messageActions.js"
import { blockHash } from "../ktui/blockId.js"
import { createAppBridge } from "./appBridge.js"
import { buildSrcdoc, readTheme } from "./appDocument.js"
import AppUnavailable from "./AppUnavailable.vue"

const MAX_SENT_LINES = 3

const props = defineProps({
  widget: { type: Object, default: null },
  host: { type: Object, default: null },
  html: { type: String, default: null },
  title: { type: String, default: "" },
  bridge: { type: Boolean, default: null },
})

const { t } = useI18n()
const opener = useMessageActions()?.openApp || null

const opening = ref(false)
const isOpen = ref(false)
const error = ref("")
const sentLines = ref([])
const sourceHtml = ref(typeof props.html === "string" ? props.html : null)

const spec = computed(() => props.widget?.spec || {})
const appTitle = computed(() => props.widget?.title || props.title || "App")
const appId = computed(() => (props.widget?.id ? `w:${props.widget.id}` : `h:${blockHash(props.html || "")}`))
const withBridge = computed(() => props.bridge ?? Boolean(props.host))

let handle = null
// Stands in for the frame window: the bridge posts here and the host relays to the tab.
const relay = { postMessage: (message) => handle?.post(message) }

function initPayload() {
  return {
    widgetId: props.widget?.id || "",
    title: appTitle.value,
    data: spec.value.data ?? null,
    state: props.widget?.state || {},
    theme: readTheme(),
    viewport: { width: 0, height: 0, mode: "tab" },
    locale: globalThis.navigator?.language || "en",
  }
}

const bridge = createAppBridge({
  getFrame: () => ({ contentWindow: relay }),
  getWidget: () => props.widget,
  host: props.host,
  getInit: initPayload,
  onSent: (name) => {
    sentLines.value = [...sentLines.value, name].slice(-MAX_SENT_LINES)
  },
  onError: (message) => {
    error.value = t("app.scriptError", { error: message })
  },
  openLink: (url) => opener?.openLink?.(url),
})

async function documentHtml() {
  if (typeof props.html === "string") return props.html
  if (typeof spec.value.html === "string") return spec.value.html
  const url = spec.value.document_url
  if (typeof url !== "string" || !url.startsWith("/api/sessions/")) throw new Error(t("app.loadError", { error: "no document" }))
  return opener.fetchDocument(url)
}

async function open() {
  if (!opener || opening.value) return
  opening.value = true
  error.value = ""
  try {
    const html = await documentHtml()
    sourceHtml.value = html
    const document = buildSrcdoc({ html, hosts: props.host ? spec.value.allowed_hosts : [], init: initPayload(), theme: readTheme(), bridge: withBridge.value })
    handle = await opener.open(
      { appId: appId.value, title: appTitle.value, document },
      {
        // The host asked the user before relaying a ``kt.send``; that answer is the interaction.
        onMessage: (message, interacted) => {
          if (interacted) bridge.noteInteraction()
          bridge.handleMessage({ source: relay, data: message })
        },
        onClose: () => {
          handle = null
          isOpen.value = false
        },
      },
    )
    isOpen.value = true
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    opening.value = false
  }
}

watch(
  () => [spec.value.sha256, spec.value.document_url, spec.value.html, props.html],
  (next, prev) => {
    if (isOpen.value && prev && next.some((v, i) => v !== prev[i])) open()
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

defineExpose({ bridge, open })
</script>

<style scoped>
.app-launcher {
  border: 1px solid var(--color-border, rgba(127, 127, 127, 0.3));
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
}
.app-launcher-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.app-launcher-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-launcher-note {
  margin-top: 4px;
  opacity: 0.75;
}
.app-launcher-error {
  margin-top: 4px;
  color: var(--color-coral, #d9534f);
}
.app-launcher-source {
  margin-top: 6px;
}
.app-launcher-source pre {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 4px 0 0;
}
</style>
