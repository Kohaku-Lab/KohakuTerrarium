<template>
  <div
    class="status-bar flex items-center gap-2 px-3 h-6 text-[10px] font-mono bg-warm-100 dark:bg-warm-950 border-t border-warm-200 dark:border-warm-700 text-warm-500 overflow-hidden shrink-0"
  >
    <!-- Instance name + status -->
    <div class="status-seg flex items-center gap-1.5 shrink-0">
      <StatusDot v-if="instance" :status="instance.status" class="scale-75" />
      <span class="truncate max-w-40">{{ instance?.config_name || '—' }}</span>
    </div>

    <div class="seg-sep" />

    <!-- Model quick switcher + config gear -->
    <ModelSwitcher />
    <button
      class="w-4 h-4 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
      title="Settings"
      @click="layout.switchPreset('settings')"
    >
      <div class="i-carbon-settings text-[10px]" />
    </button>

    <div class="seg-sep" />

    <!-- Session id (click to copy) -->
    <button
      class="status-seg flex items-center gap-1 hover:text-warm-700 dark:hover:text-warm-300 transition-colors shrink-0"
      :title="sessionId || ''"
      @click="copySession"
    >
      <span class="i-carbon-id text-[11px]" />
      <span class="truncate max-w-28">{{ sessionIdShort }}</span>
    </button>

    <!-- Spacer -->
    <div class="flex-1 min-w-0" />

    <!-- Running jobs count -->
    <div
      class="status-seg flex items-center gap-1 shrink-0"
      :class="jobCount ? 'text-amber' : ''"
    >
      <span class="i-carbon-pulse text-[11px]" />
      <span>{{ jobCount }}</span>
    </div>

    <div class="seg-sep" />

    <!-- Runtime -->
    <div class="status-seg flex items-center gap-1 shrink-0">
      <span class="i-carbon-time text-[11px]" />
      <span>{{ runtimeStr }}</span>
    </div>
  </div>

</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

import ModelSwitcher from "@/components/chrome/ModelSwitcher.vue";
import StatusDot from "@/components/common/StatusDot.vue";
import { useChatStore } from "@/stores/chat";
import { useInstancesStore } from "@/stores/instances";
import { useLayoutStore } from "@/stores/layout";

const instances = useInstancesStore();
const chat = useChatStore();
const layout = useLayoutStore();

const instance = computed(() => instances.current);
const sessionId = computed(
  () => chat.sessionInfo.sessionId || instance.value?.session_id || "",
);
const sessionIdShort = computed(() => {
  const s = sessionId.value;
  if (!s) return "—";
  return s.length > 12 ? s.slice(0, 12) + "…" : s;
});

const jobCount = computed(() => Object.keys(chat.runningJobs || {}).length);

const now = ref(Date.now());
let tick = null;
onMounted(() => {
  tick = setInterval(() => { now.value = Date.now(); }, 1000);
});
onUnmounted(() => {
  if (tick) clearInterval(tick);
});

const runtimeStr = computed(() => {
  const t0 = instance.value?.created_at;
  if (!t0) return "—";
  const started = typeof t0 === "number" ? t0 * 1000 : Date.parse(t0);
  if (!Number.isFinite(started)) return "—";
  const secs = Math.max(0, Math.floor((now.value - started) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
});

function copySession() {
  const s = sessionId.value;
  if (!s || typeof navigator === "undefined" || !navigator.clipboard) return;
  navigator.clipboard.writeText(s).catch(() => {});
}

</script>

<style scoped>
.seg-sep {
  width: 1px;
  height: 12px;
  background: currentColor;
  opacity: 0.15;
  flex-shrink: 0;
}
.status-seg {
  user-select: none;
}
</style>
