<template>
  <el-dialog :model-value="Boolean(report)" :title="t('resumeImages.title')" width="560px" :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false">
    <div v-if="report" class="space-y-3 text-sm text-warm-600 dark:text-warm-300">
      <p>{{ t("resumeImages.summary", report) }}</p>
      <p v-if="report.node">{{ t("resumeImages.worker", report) }}</p>
      <ul class="max-h-48 space-y-1 overflow-y-auto rounded bg-warm-100 px-3 py-2 dark:bg-warm-800">
        <li v-for="item in report.examples" :key="`${item.namespace}:${item.message_index}:${item.part_index}`" class="break-all font-mono text-xs">
          {{ item.namespace }}<template v-if="item.turn_index"> · {{ t("resumeImages.turn", item) }}</template
          >: {{ item.url }}
        </li>
      </ul>
      <p v-if="report.total > report.examples.length">{{ t("resumeImages.more", { count: report.total - report.examples.length }) }}</p>
      <p>{{ t(canRepair ? "resumeImages.replaceHelp" : "resumeImages.restoreHelp") }}</p>
    </div>
    <template #footer>
      <div class="flex w-full flex-wrap items-center justify-end gap-2">
        <button class="btn-secondary" @click="finish('cancel')">{{ t("common.cancel") }}</button>
        <button :class="canRepair ? 'btn-secondary' : 'btn-primary'" @click="finish('retry')">{{ t("resumeImages.retry") }}</button>
        <button v-if="canRepair" class="btn-primary" @click="finish('repair')">{{ t("resumeImages.replace") }}</button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { useI18n } from "@/utils/i18n"
import { installResumeImagesResolver } from "@/utils/resumeImages"

const { t } = useI18n()
const report = ref(null)
const canRepair = computed(() => Boolean(report.value?.confirmation) && report.value.repairable === report.value.total)
let pending = null
let uninstall = null

function finish(action) {
  const resolve = pending
  pending = null
  report.value = null
  resolve?.(action)
}

onMounted(() => {
  uninstall = installResumeImagesResolver((detail) => {
    finish("cancel")
    report.value = detail
    return new Promise((resolve) => {
      pending = resolve
    })
  })
})

onBeforeUnmount(() => {
  uninstall?.()
  finish("cancel")
})
</script>
