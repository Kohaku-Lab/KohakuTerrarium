<template>
  <!--
    InlineUiBlock: one <ui> block from an assistant reply, drawn with the kt.ui
    tree. While the reply streams it shows the last version that converted and
    validated; a block that fails shows an error box with its source, never
    breaking the rest of the message.
  -->
  <div class="kt-inline-ui" :class="{ 'kt-inline-ui-page': page, 'kt-inline-ui-fill': fill }" data-test="inline-ui">
    <div v-if="failure" class="kt-inline-ui-error" data-test="inline-ui-error">
      <div class="text-[11px] text-coral">This UI block could not be shown: {{ failure }}</div>
      <details>
        <summary class="text-[11px] text-warm-400 cursor-pointer">Source</summary>
        <pre class="ktui-code"><code>{{ source }}</code></pre>
      </details>
    </div>
    <template v-else-if="good">
      <div v-if="page && canOpenCanvas" class="kt-inline-ui-actions">
        <el-button size="small" link type="primary" data-test="inline-ui-open-canvas" @click="openCanvas">Open in canvas</el-button>
      </div>
      <UiTree :widget="widget" :host="host" />
    </template>
    <div v-else class="text-[11px] text-warm-400 px-1" data-test="inline-ui-pending">Building UI…</div>
  </div>
</template>

<script setup>
import { computed, onErrorCaptured, ref, shallowRef, watch } from "vue"
import { ElButton } from "element-plus"

import { MarkupError, convertMarkup } from "./markup.js"
import UiTree from "./UiTree.vue"
import { validateSpec } from "./validate.js"

const MAX_SHOWN_ERRORS = 3

const props = defineProps({
  source: { type: String, required: true },
  closed: { type: Boolean, default: true },
  streaming: { type: Boolean, default: false },
  canOpenCanvas: { type: Boolean, default: false },
  fill: { type: Boolean, default: false },
  // ``{key, state, set(patch), claim()}`` from AssistantText; null keeps state local.
  binding: { type: Object, default: null },
  sayBlocked: { type: String, default: "" },
})

const emit = defineEmits(["say", "open-canvas"])

const good = shallowRef(null)
const problem = ref("")
const renderError = ref("")

const final = computed(() => props.closed || !props.streaming)
const page = computed(() => good.value?.layout === "page")
const failure = computed(() => renderError.value || (final.value ? problem.value : ""))

function attempt(source, streaming) {
  let spec
  try {
    spec = convertMarkup(source, { dropOpenDiagram: streaming, holdOpenHtml: streaming })
  } catch (err) {
    return { error: err instanceof MarkupError ? err.message : String(err?.message || err) }
  }
  const errors = validateSpec(spec)
  if (!errors.length) return { spec }
  return {
    error: errors
      .slice(0, MAX_SHOWN_ERRORS)
      .map(([path, message]) => `${path}: ${message}`)
      .join("; "),
  }
}

watch(
  () => [props.source, final.value],
  () => {
    const result = attempt(props.source, !final.value)
    if (result.spec) {
      good.value = result.spec
      problem.value = ""
      renderError.value = ""
    } else {
      problem.value = result.error
    }
  },
  { immediate: true },
)

const widget = computed(() => ({ id: "", spec: good.value || {}, state: props.binding?.state || {}, closed: false, streaming: !final.value }))
const host = {
  send() {},
  setState: (patch) => props.binding?.set(patch),
  say: (text) => emit("say", text),
  get sayBlocked() {
    return props.sayBlocked
  },
}

watch(
  () => props.binding?.key,
  () => props.binding?.claim(),
  { immediate: true },
)

function openCanvas() {
  emit("open-canvas", { source: props.source, title: good.value?.title || "" })
}

onErrorCaptured((err) => {
  renderError.value = err?.message || String(err)
  return false
})
</script>

<style scoped>
.kt-inline-ui {
  margin: 0.5rem 0;
}
.kt-inline-ui-page {
  width: 100%;
}
.kt-inline-ui-fill {
  height: 100%;
  margin: 0;
  overflow: auto;
}
.kt-inline-ui-actions {
  display: flex;
  justify-content: flex-end;
}
.kt-inline-ui-error {
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.18));
  border-radius: 0.5rem;
  padding: 0.5rem 0.75rem;
}
</style>
