<template>
  <el-dialog v-model="visible" :title="t('auth.admin.title')" width="460px" :close-on-click-modal="false" align-center>
    <div class="flex flex-col gap-3 text-[13px]">
      <p class="text-[12px] text-warm-500 dark:text-warm-300">
        {{ t("auth.admin.hint") }}
      </p>
      <div>
        <label class="text-[11px] text-warm-400 block mb-1">
          {{ t("hostPicker.adminToken") }}
        </label>
        <input v-model="token" type="password" autocomplete="off" class="input-field font-mono" :placeholder="t('auth.admin.placeholder')" @keydown.enter="onSubmit" />
      </div>
      <p v-if="errorMessage" class="text-[12px] text-coral">{{ errorMessage }}</p>
      <div class="flex items-center justify-end gap-2 mt-1">
        <el-button size="small" @click="onCancel">{{ t("auth.admin.cancel") }}</el-button>
        <el-button size="small" type="primary" :disabled="!token.trim()" @click="onSubmit">
          {{ t("auth.admin.submit") }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElButton, ElDialog } from "element-plus"

import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

const auth = useAuthStore()
const { t } = useI18n()

const token = ref("")
const errorMessage = ref("")

/** The modal is visible whenever the auth store has a pending admin-
 *  prompt request (set by the api.js interceptor on a 401 +
 *  X-Auth-Required: admin response). */
const visible = computed({
  get: () => auth.pendingAdminPrompt !== null,
  set: (v) => {
    if (!v) onCancel()
  },
})

// Reset form state when the modal opens so a previous cancel doesn't
// leak a stale value into the next prompt.
watch(visible, (v) => {
  if (v) {
    token.value = ""
    errorMessage.value = ""
  }
})

function onSubmit() {
  const trimmed = token.value.trim()
  if (!trimmed) return
  auth.setAdminToken(trimmed)
  auth.resolveAdminPrompt()
}

function onCancel() {
  auth.rejectAdminPrompt()
}
</script>
