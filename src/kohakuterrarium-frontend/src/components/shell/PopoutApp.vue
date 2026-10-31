<template>
  <div class="h-full flex flex-col overflow-hidden bg-warm-50 dark:bg-warm-950" data-test="popout-app">
    <div class="h-8 shrink-0 flex items-center gap-2 px-3 text-xs border-b border-warm-200 dark:border-warm-700 text-warm-600 dark:text-warm-400">
      <span class="i-carbon-chat shrink-0" />
      <span class="truncate flex-1" data-test="popout-title">{{ title }}</span>
      <button class="shrink-0 flex items-center gap-1 px-2 py-0.5 rounded hover:bg-warm-200/60 dark:hover:bg-warm-800" data-test="popout-return" @click="returnToMain">
        <span class="i-carbon-arrow-left" />
        {{ t("popout.returnToMain") }}
      </button>
    </div>
    <div class="flex-1 min-h-0">
      <AuthGate>
        <AttachTab :tab="tab" />
      </AuthGate>
    </div>
    <ToastCenter />
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue"

import AuthGate from "@/components/auth/AuthGate.vue"
import ToastCenter from "@/components/chrome/ToastCenter.vue"
import AttachTab from "@/components/shell/tabs/AttachTab.vue"
import { useInstancesStore } from "@/stores/instances"
import { ackPopoutWindow, returnPopoutWindow } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

/** One session shown alone in a native window; the shell opens it with `?kt_popout=<scope>`. */
const props = defineProps({
  scope: { type: String, required: true },
})

const { t } = useI18n()
const tab = computed(() => ({ id: `popout:${props.scope}`, kind: "attach", target: props.scope }))
const instances = useInstancesStore()
const title = computed(() => {
  const match = (it) => it && (it.id === props.scope || it.graph_id === props.scope)
  const instance = match(instances.current) ? instances.current : instances.list.find(match)
  return instance?.session_name || instance?.config_name || "KohakuTerrarium"
})

function returnToMain() {
  void returnPopoutWindow(true)
}

instances.fetchAll()
onMounted(() => {
  void ackPopoutWindow()
  document.title = "KohakuTerrarium"
})
</script>
