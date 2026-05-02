<template>
  <ModalShell @close="$emit('close')">
    <template #title>Stop instance</template>
    <p class="text-sm text-warm-700 dark:text-warm-300">
      Stop
      <span class="font-medium">{{ instance.config_name }}</span>
      ({{ instance.type }})? Active connections will be closed.
    </p>
    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="$emit('close')">Cancel</button>
        <button class="btn-primary text-xs px-3 py-1.5 bg-coral hover:bg-coral-dark" :disabled="stopping" @click="onStop">
          {{ stopping ? "Stopping…" : "Stop" }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { ref } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({ instance: { type: Object, required: true } })
const emit = defineEmits(["close", "stopped"])

const instances = useInstancesStore()
const tabs = useTabsStore()
const stopping = ref(false)

async function onStop() {
  stopping.value = true
  try {
    await instances.stop(props.instance.id)
    // Close any open surface tabs for this target
    await tabs.detach(props.instance.id)
    emit("stopped")
  } catch (err) {
    console.error("Stop failed:", err)
  } finally {
    stopping.value = false
  }
}
</script>
