<template>
  <div class="h-full overflow-hidden bg-warm-50 dark:bg-warm-950" data-test="popout-app">
    <AuthGate>
      <AttachTab :tab="tab" />
    </AuthGate>
    <ToastCenter />
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue"

import AuthGate from "@/components/auth/AuthGate.vue"
import ToastCenter from "@/components/chrome/ToastCenter.vue"
import AttachTab from "@/components/shell/tabs/AttachTab.vue"
import { useInstancesStore } from "@/stores/instances"
import { ackPopoutWindow } from "@/utils/hostBridge"

/** One session shown alone in a native window; the shell opens it with `?kt_popout=<scope>`. */
const props = defineProps({
  scope: { type: String, required: true },
})

const tab = computed(() => ({ id: `popout:${props.scope}`, kind: "attach", target: props.scope }))
const instances = useInstancesStore()

instances.fetchAll()
onMounted(() => {
  void ackPopoutWindow()
  document.title = "KohakuTerrarium"
})
</script>
