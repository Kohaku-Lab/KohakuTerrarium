<template>
  <!-- kt.ui tree: nested nodes that open and close locally. -->
  <ul class="ktui-tree" :class="{ 'ktui-tree-root': depth === 0 }" :role="depth === 0 ? 'tree' : 'group'" :data-test="depth === 0 ? 'ktui-tree' : undefined">
    <li v-for="(node, i) in nodes" :key="i" class="ktui-tree-item" role="treeitem" :aria-expanded="node.children.length ? isOpen(i, node) : undefined">
      <button v-if="node.children.length" type="button" class="ktui-tree-row" data-test="ktui-tree-toggle" @click="toggle(i, node)">
        <span class="ktui-tree-caret" aria-hidden="true">{{ isOpen(i, node) ? "▾" : "▸" }}</span
        >{{ node.label }}<span v-if="node.badge" class="ktui-badge ktui-tree-badge">{{ node.badge }}</span>
      </button>
      <span v-else class="ktui-tree-row ktui-tree-leaf"
        >{{ node.label }}<span v-if="node.badge" class="ktui-badge ktui-tree-badge">{{ node.badge }}</span></span
      >
      <UiTreeList v-if="node.children.length && isOpen(i, node)" :items="node.children" :open="open" :depth="depth + 1" :display="display" :prefix="keyOf(i)" :toggled="toggled" @toggle="(key, value) => emit('toggle', key, value)" />
    </li>
  </ul>
</template>

<script setup>
import { computed, ref } from "vue"

defineOptions({ name: "UiTreeList" })

const MAX_DEPTH = 12

const props = defineProps({
  items: { type: Array, default: () => [] },
  open: { type: Boolean, default: true },
  depth: { type: Number, default: 0 },
  display: { type: Function, default: (v) => String(v ?? "") },
  // Saved open/closed nodes of the whole tree, keyed by index path ("0.2").
  toggled: { type: Object, default: () => ({}) },
  prefix: { type: String, default: "" },
})

const emit = defineEmits(["toggle"])
const local = ref({})

const nodes = computed(() =>
  props.items.map((item) => {
    const raw = item && typeof item === "object" ? item : { label: item }
    const children = props.depth < MAX_DEPTH && Array.isArray(raw.children) ? raw.children : []
    return {
      label: props.display(raw.label),
      badge: raw.badge != null ? props.display(raw.badge) : "",
      children,
      open: typeof raw.open === "boolean" ? raw.open : props.open,
    }
  }),
)

function keyOf(i) {
  return props.prefix ? `${props.prefix}.${i}` : String(i)
}

function isOpen(i, node) {
  const key = keyOf(i)
  if (typeof props.toggled[key] === "boolean") return props.toggled[key]
  return key in local.value ? local.value[key] : node.open
}

function toggle(i, node) {
  const value = !isOpen(i, node)
  local.value = { ...local.value, [keyOf(i)]: value }
  emit("toggle", keyOf(i), value)
}
</script>
