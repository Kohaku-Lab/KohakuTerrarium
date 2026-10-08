<template>
  <section ref="rootEl" class="kt-v2-float kt-v2-edge w-[22rem] max-w-[calc(100vw-2rem)] h-[50vh] min-h-64 flex flex-col rounded-xl border shadow-xl overflow-hidden" :data-test="`v2-widget-${id}`">
    <header class="kt-v2-chrome kt-v2-line h-9 shrink-0 flex items-center gap-2 px-3 border-b text-xs font-medium text-warm-700 dark:text-warm-200">
      <span class="truncate">{{ t(`widget.${id}.title`) }}</span>
      <span class="flex-1" />
      <button class="i-carbon-side-panel-open text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('dock.pin')" @click="ctx.pinWidget(id)" />
      <button class="i-carbon-close text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('close')" @click="ctx.closeWidget()" />
    </header>
    <div class="flex-1 min-h-0 overflow-hidden">
      <component :is="WIDGETS[id]" v-if="WIDGETS[id]" mode="widget" />
    </div>
  </section>
</template>

<script setup>
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from "vue"

import { WIDGETS } from "@/components/session-v2/model/registry"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The floating card a dock widget opens in: title, pin to side view, close; a click outside it and the dock closes it. */
const props = defineProps({ id: { type: String, required: true } })

const ctx = useSessionV2()
const t = useV2T()
const rootEl = ref(null)

function onPointerDown(e) {
  if (!rootEl.value || rootEl.value.contains(e.target)) return
  if (e.target.closest?.("[data-test='v2-dock']")) return
  // Element Plus poppers render outside the card; clicks in them belong to the widget.
  if (e.target.closest?.(".el-popper, .el-overlay")) return
  if (ctx.widget.value === props.id) ctx.closeWidget()
}
const listen = () => document.addEventListener("pointerdown", onPointerDown, true)
const unlisten = () => document.removeEventListener("pointerdown", onPointerDown, true)
onMounted(listen)
onActivated(listen)
onDeactivated(unlisten)
onBeforeUnmount(unlisten)
</script>
