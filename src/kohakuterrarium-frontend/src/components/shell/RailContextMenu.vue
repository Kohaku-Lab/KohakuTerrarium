<template>
  <div class="fixed inset-0 z-40" @click="$emit('close')" @contextmenu.prevent="$emit('close')">
    <div class="absolute z-50 w-48 bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 rounded shadow-lg py-1 text-xs" :style="{ left: '60px', top: '100px' }" @click.stop>
      <div class="px-3 py-1 text-warm-400 truncate text-[10px] uppercase tracking-wider border-b border-warm-200 dark:border-warm-700 mb-1">
        {{ instance.config_name }}
      </div>
      <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" @click="emitAndClose('toggle-chat')">
        <span class="i-carbon-chat" />
        {{ chatOpen ? "Close chat" : "Open chat" }}
      </button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" @click="emitAndClose('toggle-inspector')">
        <span class="i-carbon-radar" />
        {{ inspectorOpen ? "Close inspector" : "Open inspector" }}
      </button>
      <div class="border-t border-warm-200 dark:border-warm-700 my-1" />
      <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" :disabled="!chatOpen && !inspectorOpen" :class="{ 'opacity-50 cursor-not-allowed': !chatOpen && !inspectorOpen }" @click="emitAndClose('detach')">
        <span class="i-carbon-disconnect" />
        Detach (close all surfaces)
      </button>
      <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2 text-coral" disabled :class="{ 'opacity-50 cursor-not-allowed': true }" @click="emitAndClose('stop')">
        <span class="i-carbon-stop-filled" />
        Stop instance (Phase 5)
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  instance: { type: Object, required: true },
  chatOpen: { type: Boolean, default: false },
  inspectorOpen: { type: Boolean, default: false },
})
const emit = defineEmits(["close", "toggle-chat", "toggle-inspector", "detach", "stop"])

function emitAndClose(name) {
  emit(name)
  emit("close")
}
</script>
