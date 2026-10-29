<template>
  <!-- One kt.ui node; layout nodes recurse into UiNode for their children. -->
  <template v-if="visible">
    <div v-if="type === 'stack'" class="ktui-stack" :class="[`ktui-gap-${node.gap || 'md'}`, `ktui-align-${node.align || 'stretch'}`, { 'ktui-row': node.direction === 'row', 'ktui-wrap': flag(node.wrap) }]">
      <UiNode v-for="(child, i) in children" :key="i" :node="child" :scope="scope" />
    </div>
    <div v-else-if="type === 'grid'" class="ktui-grid" :class="`ktui-gap-${node.gap || 'md'}`" :style="{ gridTemplateColumns: `repeat(${gridColumns}, minmax(0, 1fr))` }">
      <UiNode v-for="(child, i) in children" :key="i" :node="child" :scope="scope" />
    </div>
    <div v-else-if="type === 'card'" class="ktui-card">
      <div v-if="node.title || node.subtitle" class="ktui-card-header">
        <span class="font-semibold text-sm">{{ text(node.title) }}</span>
        <span v-if="node.subtitle" class="text-xs text-warm-500">{{ text(node.subtitle) }}</span>
      </div>
      <UiNode v-for="(child, i) in children" :key="i" :node="child" :scope="scope" />
    </div>
    <section v-else-if="type === 'section'" class="ktui-section">
      <div v-if="node.title" class="ktui-section-title">{{ text(node.title) }}</div>
      <UiNode v-for="(child, i) in children" :key="i" :node="child" :scope="scope" />
    </section>
    <el-tabs v-else-if="type === 'tabs'" v-model="activeTab" class="ktui-tabs">
      <template v-for="(tab, i) in children" :key="i">
        <el-tab-pane v-if="isShown(tab)" :label="text(tab.label)" :name="String(i)">
          <UiNode v-for="(child, j) in tab.children || []" :key="j" :node="child" :scope="scope" />
        </el-tab-pane>
      </template>
    </el-tabs>
    <hr v-else-if="type === 'divider'" class="ktui-divider" />

    <div v-else-if="type === 'text'" class="ktui-text" :class="[`ktui-text-${node.size || 'md'}`, { 'text-warm-500': flag(node.muted) }]">
      <MarkdownRenderer :content="text(node.value)" :breaks="true" :origin="markdownOrigin" />
    </div>
    <component :is="`h${headingLevel}`" v-else-if="type === 'heading'" class="ktui-heading" :class="`ktui-h${headingLevel}`">{{ text(node.value) }}</component>
    <span v-else-if="type === 'badge'" class="ktui-badge" :class="`ktui-tone-${node.tone || 'neutral'}`">{{ text(node.value) }}</span>
    <div v-else-if="type === 'stat'" class="ktui-stat" :class="`ktui-tone-${node.tone || 'neutral'}`">
      <div v-if="node.label" class="ktui-stat-label">{{ text(node.label) }}</div>
      <div class="ktui-stat-value" data-test="ktui-stat-value">{{ text(node.value) }}</div>
      <div v-if="node.hint" class="ktui-stat-hint">{{ text(node.hint) }}</div>
    </div>
    <dl v-else-if="type === 'kv'" class="ktui-kv">
      <template v-for="(field, i) in listOf(node.fields)" :key="i">
        <dt>{{ display(field?.label) }}</dt>
        <dd>{{ display(field?.value) }}</dd>
      </template>
    </dl>
    <component :is="flag(node.ordered) ? 'ol' : 'ul'" v-else-if="type === 'list'" class="ktui-list">
      <li v-for="(item, i) in listOf(node.items)" :key="i">{{ display(item) }}</li>
    </component>
    <div v-else-if="type === 'table'" class="ktui-table-wrap">
      <table class="ktui-table">
        <thead>
          <tr>
            <th v-for="col in columns" :key="col.index" :style="{ textAlign: col.align }">{{ display(col.label) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in listOf(node.rows)" :key="r">
            <td v-for="col in columns" :key="col.index" :style="{ textAlign: col.align }">{{ display(tableCell(row, col)) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <img v-else-if="type === 'image' && imageSrc" class="ktui-image" :src="imageSrc" :alt="text(node.alt || '')" :style="node.width ? { maxWidth: `${node.width}px` } : {}" />
    <div v-else-if="type === 'image'" class="ktui-note">image not shown: {{ text(node.alt || "unsupported source") }}</div>
    <pre v-else-if="type === 'code'" class="ktui-code"><code>{{ text(node.value) }}</code></pre>

    <UiChart v-else-if="type === 'chart'" :kind="node.kind" :labels="listOf(node.labels)" :series="seriesOf(node.series)" :title="node.title ? text(node.title) : ''" :height="num(node.height, 200)" />
    <div v-else-if="type === 'diagram'" class="ktui-diagram">
      <div v-if="node.title" class="ktui-section-title">{{ text(node.title) }}</div>
      <pre class="ktui-code"><code>{{ text(node.source) }}</code></pre>
      <div class="ktui-note">diagram source (rendering is not available here)</div>
    </div>

    <label v-else-if="isInput" class="ktui-field" :class="`ktui-field-${type}`">
      <span v-if="node.label" class="ktui-label">{{ text(node.label) }}</span>
      <el-input-number v-if="type === 'number'" :model-value="numberValue" :min="num(node.min, -Infinity)" :max="num(node.max, Infinity)" :step="num(node.step, 1)" :disabled="ctx.disabled" size="small" controls-position="right" @update:model-value="(v) => set(v ?? null)" />
      <div v-else-if="type === 'slider'" class="ktui-slider">
        <el-slider :model-value="numberValue ?? num(node.min, 0)" :min="num(node.min, 0)" :max="num(node.max, 100)" :step="num(node.step, 1)" :disabled="ctx.disabled" @update:model-value="set" />
        <span class="ktui-slider-value">{{ display(bound) }}</span>
      </div>
      <el-input v-else-if="type === 'text_input'" :model-value="bound ?? ''" :placeholder="text(node.placeholder || '')" :type="flag(node.multiline) ? 'textarea' : 'text'" :autosize="flag(node.multiline) ? { minRows: 2, maxRows: 8 } : undefined" :disabled="ctx.disabled" size="small" @update:model-value="set" />
      <el-select v-else-if="type === 'select'" :model-value="bound" :disabled="ctx.disabled" size="small" @update:model-value="set">
        <el-option v-for="(opt, i) in optionsOf(node.options)" :key="i" :label="opt.label" :value="opt.value" />
      </el-select>
      <el-radio-group v-else-if="type === 'radio'" :model-value="bound" :disabled="ctx.disabled" @update:model-value="set">
        <el-radio v-for="(opt, i) in optionsOf(node.options)" :key="i" :value="opt.value">{{ opt.label }}</el-radio>
      </el-radio-group>
      <el-switch v-else-if="type === 'toggle'" :model-value="bound === true" :disabled="ctx.disabled" @update:model-value="set" />
      <el-checkbox v-else-if="type === 'checkbox'" :model-value="bound === true" :disabled="ctx.disabled" @update:model-value="set" />
      <el-date-picker v-else-if="type === 'date'" :model-value="bound || ''" type="date" value-format="YYYY-MM-DD" :disabled="ctx.disabled" size="small" @update:model-value="set" />
    </label>

    <el-button v-else-if="type === 'button'" :type="buttonType" :disabled="ctx.disabled || flagValue(node.disabled)" size="small" data-test="ktui-button" @click="press">{{ text(node.label) }}</el-button>
    <form v-else-if="type === 'form'" class="ktui-form" @submit.prevent="submit">
      <UiNode v-for="(child, i) in children" :key="i" :node="child" :scope="scope" />
      <div>
        <el-button type="primary" native-type="submit" size="small" :disabled="ctx.disabled" data-test="ktui-submit">{{ text(node.submit || "Submit") }}</el-button>
      </div>
    </form>

    <template v-else-if="type === 'each'">
      <div v-if="each.error" class="ktui-note" :title="each.error">⚠ {{ each.error }}</div>
      <UiNode v-for="(item, i) in each.items" :key="i" :node="node.child" :scope="{ ...scope, [node.as || 'item']: item, index: i }" />
    </template>
    <div v-else class="ktui-note">unsupported component: {{ type }}</div>
  </template>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElCheckbox, ElDatePicker, ElInput, ElInputNumber, ElOption, ElRadio, ElRadioGroup, ElSelect, ElSlider, ElSwitch, ElTabPane, ElTabs } from "element-plus"

import MarkdownRenderer from "../../../../public/chat/MarkdownRenderer.vue"
import { usePlatformOrigin } from "../../../../public/chat/platformOrigin.js"
import { useKtUi } from "./context.js"
import { evaluate, toDisplay, truthy } from "./expr.js"
import { boundNames, eachItems, isVisible, normalizeOptions, resolve, resolveDeep, resolveText, safeImageSrc, tableCell, tableColumns } from "./runtime.js"
import UiChart from "./UiChart.vue"

defineOptions({ name: "UiNode" })

const props = defineProps({
  node: { type: Object, required: true },
  scope: { type: Object, required: true },
})

const INPUTS = new Set(["number", "slider", "text_input", "select", "radio", "toggle", "checkbox", "date"])

const ctx = useKtUi()
const markdownOrigin = usePlatformOrigin()
const activeTab = ref("0")

const type = computed(() => props.node?.type)
const visible = computed(() => !!props.node && typeof props.node === "object" && isVisible(props.node, props.scope))
const children = computed(() => (Array.isArray(props.node.children) ? props.node.children : []))
const isInput = computed(() => INPUTS.has(type.value))
const bound = computed(() => (typeof props.node.bind === "string" ? (props.scope[props.node.bind] ?? null) : null))
const numberValue = computed(() => (typeof bound.value === "number" ? bound.value : null))
const columns = computed(() => tableColumns(valueOf(props.node.columns)))
const each = computed(() => (type.value === "each" ? eachItems(props.node, props.scope) : { items: [], error: "" }))
const imageSrc = computed(() => safeImageSrc(valueOf(props.node.src)))
const headingLevel = computed(() => {
  const level = num(props.node.level, 2)
  return Math.min(4, Math.max(1, Math.round(level)))
})
const gridColumns = computed(() => Math.min(12, Math.max(1, Math.round(num(props.node.columns, 2)))))
const buttonType = computed(() => ({ primary: "primary", danger: "danger" })[props.node.style] || "")

function valueOf(value) {
  return resolve(value, props.scope).value
}

function text(value) {
  return resolveText(value, props.scope)
}

function display(value) {
  return typeof value === "string" ? text(value) : toDisplay(value)
}

function listOf(value) {
  const out = valueOf(value)
  return Array.isArray(out) ? out : []
}

function optionsOf(value) {
  return normalizeOptions(listOf(value))
}

function seriesOf(value) {
  return listOf(value).map((s) => (s && typeof s === "object" ? { name: display(s.name ?? ""), data: Array.isArray(valueOf(s.data)) ? valueOf(s.data) : [] } : { name: "", data: [] }))
}

function num(value, fallback) {
  const out = valueOf(value)
  return typeof out === "number" && Number.isFinite(out) ? out : fallback
}

function flag(value) {
  return value === true
}

function flagValue(value) {
  if (value === undefined) return false
  const { value: out, error } = resolve(value, props.scope)
  return !error && truthy(out)
}

function isShown(node) {
  return node && typeof node === "object" && isVisible(node, props.scope)
}

function set(value) {
  if (typeof props.node.bind === "string") ctx.setVar(props.node.bind, value)
}

function press() {
  const node = props.node
  if (node.set && typeof node.set === "object") {
    const updates = {}
    for (const [key, expr] of Object.entries(node.set)) {
      try {
        updates[key] = evaluate(expr, props.scope)
      } catch {
        // A failed assignment leaves that key unchanged.
      }
    }
    ctx.setVars(updates)
  }
  if (typeof node.send === "string") ctx.send(node.send, resolveDeep(node.data || {}, props.scope))
}

function submit() {
  const values = {}
  for (const name of boundNames(props.node)) values[name] = props.scope[name] ?? null
  const extra = resolveDeep(props.node.data || {}, props.scope)
  ctx.send(props.node.send, { ...values, ...(extra && typeof extra === "object" ? extra : {}) })
}
</script>
