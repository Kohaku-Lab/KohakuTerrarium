<template>
  <!-- kt.ui renderer root: owns the live state, the scope and the actions its nodes use. -->
  <div class="ktui" :class="[layout === 'page' ? 'ktui-page' : 'ktui-inline', { 'ktui-closed': widget.closed }]" data-test="ktui">
    <div v-if="titleText" class="ktui-title" :class="{ 'ktui-page-header': layout === 'page' }">{{ titleText }}</div>
    <UiNode v-if="spec.root" :node="spec.root" :scope="scope" />
  </div>
</template>

<script setup>
import { computed, provide, reactive, watch } from "vue"

import "./ktui.css"
import { KTUI_KEY } from "./context.js"
import { buildScope, computedOrder, resolveText } from "./runtime.js"
import UiNode from "./UiNode.vue"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => (props.widget.spec && typeof props.widget.spec === "object" ? props.widget.spec : {}))
const layout = computed(() => (spec.value.layout === "page" ? "page" : "inline"))
const order = computed(() => computedOrder(spec.value.computed))
const local = reactive({})

function adopt(source) {
  for (const [key, value] of Object.entries(source || {})) local[key] = value
}

adopt(spec.value.state)
adopt(props.widget.state)
watch(
  () => spec.value.state,
  (state) => {
    for (const [key, value] of Object.entries(state || {})) if (!(key in local)) local[key] = value
  },
)
watch(() => props.widget.state, adopt, { deep: true })

const scope = computed(() => buildScope(spec.value, local, order.value))
const titleText = computed(() => (spec.value.title ? resolveText(spec.value.title, scope.value) : ""))
const disabled = computed(() => props.widget.closed === true)

provide(KTUI_KEY, {
  get disabled() {
    return disabled.value
  },
  setVar(name, value) {
    if (disabled.value) return
    local[name] = value
    props.host.setState({ [name]: value })
  },
  setVars(values) {
    if (disabled.value || !Object.keys(values).length) return
    Object.assign(local, values)
    props.host.setState({ ...values })
  },
  send(name, data) {
    if (disabled.value) return
    props.host.send(name, data || {})
  },
})
</script>
