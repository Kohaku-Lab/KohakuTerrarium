<template>
  <div class="rounded-lg border border-coral/40 bg-coral/8 p-3 flex flex-col gap-2" role="alertdialog" :aria-label="request.title">
    <div class="text-sm font-semibold text-coral">{{ request.title }}</div>
    <div class="flex flex-col gap-0.5 text-xs text-warm-600 dark:text-warm-300">
      <div v-for="(line, i) in request.lines" :key="i" :class="line.startsWith('·') ? 'font-mono pl-2' : ''">{{ line }}</div>
    </div>
    <div class="flex justify-end gap-2 pt-1">
      <button class="btn-secondary text-xs px-3 py-1" @click="$emit('cancel')">{{ t("graph.action.cancel") }}</button>
      <button ref="confirmBtn" class="text-xs px-3 py-1 rounded bg-coral text-white hover:bg-coral-shadow disabled:opacity-50" :disabled="busy" @click="onConfirm">{{ request.confirmLabel }}</button>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from "vue"

import { useI18n } from "@/utils/i18n"

const props = defineProps({ request: { type: Object, required: true } })
const emit = defineEmits(["cancel", "done"])

const { t } = useI18n()
const busy = ref(false)
const confirmBtn = ref(null)

onMounted(() => nextTick(() => confirmBtn.value?.focus()))

async function onConfirm() {
  busy.value = true
  try {
    await props.request.run()
  } finally {
    busy.value = false
    emit("done")
  }
}
</script>
