<template>
  <el-dialog v-model="open" :title="t('workspaceResume.title')" width="560px" :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false">
    <p class="text-sm text-warm-600 dark:text-warm-300">{{ t("workspaceResume.body") }}</p>
    <p class="mt-3 rounded bg-warm-100 px-3 py-2 font-mono text-xs text-warm-700 dark:bg-warm-800 dark:text-warm-200">
      {{ pending?.label }}
    </p>
    <template #footer>
      <div class="flex w-full items-center justify-end gap-2">
        <button class="btn-secondary" data-testid="workspace-resume-history" @click="finish({ action: 'history' })">{{ t("workspaceResume.viewHistory") }}</button>
        <button class="btn-secondary" data-testid="workspace-resume-cancel" @click="finish({ action: 'cancel' })">{{ t("workspaceResume.cancel") }}</button>
        <button class="btn-primary" data-testid="workspace-resume-choose" @click="pickerOpen = true">{{ t("workspaceResume.chooseFolder") }}</button>
      </div>
    </template>
  </el-dialog>

  <DirectoryPickerDialog v-model="pickerOpen" :initial-path="pending?.gap?.saved_pwd || pending?.gap?.path || ''" :on-node="pending?.gap?.onNode || ''" @pick="onPick" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue"
import DirectoryPickerDialog from "@/components/common/DirectoryPickerDialog.vue"
import { useI18n } from "@/utils/i18n"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

const { t } = useI18n()
const open = ref(false)
const pickerOpen = ref(false)
const pending = ref(null)
let resolvePending = null
let uninstall = null

function request(detail) {
  if (resolvePending) resolvePending({ action: "cancel" })
  pending.value = detail
  open.value = true
  pickerOpen.value = false
  return new Promise((resolve) => {
    resolvePending = resolve
  })
}

function finish(result) {
  const resolve = resolvePending
  resolvePending = null
  pickerOpen.value = false
  open.value = false
  pending.value = null
  resolve?.(result)
}

function onPick(path) {
  finish({ action: "choose", path })
}

onMounted(() => {
  uninstall = installWorkspaceResumeResolver(request)
})

onBeforeUnmount(() => {
  uninstall?.()
  if (resolvePending) finish({ action: "cancel" })
})
</script>
