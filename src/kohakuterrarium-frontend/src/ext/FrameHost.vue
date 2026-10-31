<template>
  <div v-if="error" class="kt-ext-notice !text-coral" data-test="ext-frame-error">
    <span class="i-carbon-warning-alt" />
    <div class="min-w-0">
      <div class="font-medium">{{ contribution.label }} ({{ pkg }}) failed</div>
      <pre class="text-[11px] whitespace-pre-wrap break-words">{{ error }}</pre>
    </div>
  </div>
  <iframe v-else-if="srcdoc" ref="frameEl" :sandbox="FRAME_SANDBOX" :srcdoc="srcdoc" referrerpolicy="no-referrer" :title="contribution.label" class="kt-ext-frame" :style="{ height: inline ? `${frameHeight}px` : '100%' }" data-test="ext-frame" />
  <div v-else class="kt-ext-notice" data-test="ext-frame-loading">
    <span class="i-carbon-renew kohaku-pulse" />
    <span>Loading {{ contribution.label }}…</span>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus"
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { FRAME_SANDBOX } from "@/ext/frameDocument"
import { createFrameBridge } from "@/ext/sdk/frameBridge"
import { FILE_VIEW_INJECT_KEY, GRAPH_NODE_INJECT_KEY, MESSAGE_BLOCK_INJECT_KEY, MESSAGE_CONTEXT_INJECT_KEY } from "@/ext/sdk/keys"
import { THEME_TOKENS } from "@/ext/sdk/theme"
import { contributionKey, findContribution, runtime } from "@/ext/state"
import { useLocaleStore } from "@/stores/locale"
import { useTabsStore } from "@/stores/tabs"
import { useThemeStore } from "@/stores/theme"
import api from "@/utils/api"

/**
 * Draws an `isolation: frame` contribution: the package's page in a sandboxed
 * iframe, reached only through the frame bridge. The frame sees its package's
 * API, its owner and the subject it is drawn for (a message, file or graph
 * node), and nothing else of the dashboard.
 */
const props = defineProps({
  pkg: { type: String, required: true },
  contribution: { type: Object, required: true },
  /** The owner client of this contribution (see `createOwnerClient`). */
  owner: { type: Object, required: true },
  /** Whether the frame sits in the flow of a page (height follows its content) instead of filling its mount point. */
  inline: { type: Boolean, default: false },
})

const QUIET = { __kt_auth_retry: true }
const DEFAULT_HEIGHT = 40

const frameEl = ref(null)
const srcdoc = ref("")
const error = ref("")
const frameHeight = ref(DEFAULT_HEIGHT)
const theme = useThemeStore()
const locale = useLocaleStore()
const block = inject(MESSAGE_BLOCK_INJECT_KEY, null)
const message = inject(MESSAGE_CONTEXT_INJECT_KEY, null)
const file = inject(FILE_VIEW_INJECT_KEY, null)
const node = inject(GRAPH_NODE_INJECT_KEY, null)

const subject = computed(() => {
  if (block) return { kind: "message-block", id: block.id, source: block.source.value }
  if (message) return { kind: "message", ...message.value }
  if (file) return { kind: "file", ...file.value }
  if (node) return { kind: "graph-node", ...node.value }
  return null
})

function tokens() {
  if (typeof document === "undefined") return {}
  const style = getComputedStyle(document.documentElement)
  return Object.fromEntries(THEME_TOKENS.map((name) => [name, style.getPropertyValue(name).trim()]))
}

function context() {
  return {
    package: props.pkg,
    id: props.contribution.id,
    locale: locale.locale,
    dark: Boolean(theme.dark),
    tokens: tokens(),
    subject: subject.value,
  }
}

async function callApi({ method, path, data }) {
  const url = `/ext/${encodeURIComponent(props.pkg)}/${path.replace(/^\/+/, "")}`
  const response = await api.request({ method, url, data, ...QUIET })
  return { status: response.status, data: response.data }
}

// A frame may open only the tab contributions of its own package.
function openPackageTab(id) {
  const tab = findContribution(props.pkg, id)
  if (tab?.kind !== "tab") return
  const key = contributionKey(props.pkg, id)
  useTabsStore().openTab({ kind: key, id: key, label: tab.label })
}

const bridge = createFrameBridge({
  target: () => frameEl.value?.contentWindow || null,
  handlers: {
    context,
    api: callApi,
    ownerCall: (action, args) => props.owner.call(action, args),
    notify: (text, type) => ElMessage({ message: text, type }),
    openTab: openPackageTab,
    resize: (height) => {
      if (props.inline) frameHeight.value = height
    },
    say: (text) => block?.say(text),
    hello: connectOwner,
  },
})

let offOwner = null

function connectOwner() {
  props.owner.start()
  offOwner?.()
  offOwner = props.owner.on("*", (event, data) => {
    if (event !== "state") bridge.postOwnerEvent(event, data)
  })
  bridge.postOwnerState(props.owner.state.value)
}

watch(
  () => props.owner.state.value,
  (state) => bridge.postOwnerState(state),
)
watch([() => theme.dark, () => locale.locale, subject], () => bridge.postContext(), { deep: true })

onMounted(async () => {
  bridge.start()
  try {
    srcdoc.value = await runtime.loadFrameDocument(props.pkg)
    await nextTick()
  } catch (err) {
    error.value = err?.message || String(err)
  }
})

onBeforeUnmount(() => {
  offOwner?.()
  bridge.stop()
})
</script>

<style scoped>
.kt-ext-frame {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
}
.kt-ext-notice {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 1rem;
  font-size: 12px;
  color: var(--color-text-muted);
}
</style>
