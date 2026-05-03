<template>
  <ModalShell @close="onCancel">
    <template #title>{{ title }}</template>

    <form class="space-y-3" @submit.prevent="onSubmit">
      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1">
          {{ inputLabel }}
        </label>
        <input ref="inputEl" v-model="value" type="text" required class="input-field w-full font-mono text-xs" :placeholder="placeholder" @keydown.enter.prevent="onSubmit" />
      </div>
      <div v-if="hint" class="text-[11px] text-warm-500">{{ hint }}</div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="onCancel">Cancel</button>
        <button class="btn-primary text-xs px-3 py-1.5" :disabled="!canSubmit" @click="onSubmit">
          {{ submitLabel }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"

const props = defineProps({
  title: { type: String, default: "Name" },
  inputLabel: { type: String, default: "Name" },
  placeholder: { type: String, default: "" },
  initial: { type: String, default: "" },
  submitLabel: { type: String, default: "Save" },
  hint: { type: String, default: "" },
})

const emit = defineEmits(["submit", "close"])

const value = ref(props.initial)
const inputEl = ref(null)

onMounted(async () => {
  await nextTick()
  inputEl.value?.focus()
  inputEl.value?.select()
})

const canSubmit = computed(() => value.value.trim().length > 0)

function onSubmit() {
  if (!canSubmit.value) return
  emit("submit", value.value.trim())
}

function onCancel() {
  emit("close")
}
</script>
