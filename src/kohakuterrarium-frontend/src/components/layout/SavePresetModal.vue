<template>
  <el-dialog
    v-model="open"
    title="Save layout as new preset"
    width="420px"
    :close-on-click-modal="true"
    @close="$emit('cancel')"
  >
    <div class="flex flex-col gap-3 text-xs">
      <div>
        <label class="block text-warm-500 mb-1">Name</label>
        <el-input v-model="name" placeholder="My layout" size="small" autofocus />
      </div>
      <div>
        <label class="block text-warm-500 mb-1">Shortcut (optional)</label>
        <el-select
          v-model="shortcut"
          placeholder="No shortcut"
          size="small"
          clearable
          class="w-full"
        >
          <el-option v-for="s in availableShortcuts" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      <div v-if="errorMsg" class="text-coral text-[11px]">{{ errorMsg }}</div>
    </div>

    <template #footer>
      <el-button size="small" @click="$emit('cancel')">Cancel</el-button>
      <el-button size="small" type="primary" :disabled="!name.trim()" @click="onSave">
        Save
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useLayoutStore } from "@/stores/layout"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "saved", "cancel"])

const layout = useLayoutStore()

const open = ref(props.modelValue)
watch(
  () => props.modelValue,
  (v) => {
    open.value = v
  },
)
watch(open, (v) => {
  emit("update:modelValue", v)
})

const name = ref("")
const shortcut = ref("")
const errorMsg = ref("")

// Shortcuts not already taken by existing presets.
const ALL_SHORTCUTS = [
  "Ctrl+1",
  "Ctrl+2",
  "Ctrl+3",
  "Ctrl+4",
  "Ctrl+5",
  "Ctrl+6",
  "Ctrl+7",
  "Ctrl+8",
  "Ctrl+9",
]
const availableShortcuts = computed(() => {
  const taken = new Set(
    Object.values(layout.allPresets || {})
      .map((p) => p?.shortcut)
      .filter(Boolean),
  )
  return ALL_SHORTCUTS.filter((s) => !taken.has(s))
})

function onSave() {
  const n = name.value.trim()
  if (!n) return
  const id = n.toLowerCase().replace(/[^a-z0-9-_]+/g, "-")
  if (layout.allPresets[id]) {
    errorMsg.value = `A preset named "${id}" already exists.`
    return
  }
  const saved = layout.saveAsNewPreset(id, n, shortcut.value || "")
  if (saved) {
    emit("saved", saved)
    open.value = false
    name.value = ""
    shortcut.value = ""
    errorMsg.value = ""
  }
}
</script>
