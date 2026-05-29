<template>
  <!-- Global login dialog.  Mirrors AdminTokenModal: it surfaces
       whenever the auth store has a pending login prompt — set either
       by the api.js 401 + ``X-Auth-Required: user`` interceptor, or by
       any proactive ``auth.requestLogin()`` caller (e.g. the host
       picker's "Log in").  LoginPane drives the actual credentials +
       calls ``auth.login()`` / ``auth.register()``, which resolve the
       pending prompt on success — flipping ``visible`` shut. -->
  <el-dialog v-model="visible" :title="t('auth.login.title')" width="460px" :close-on-click-modal="false" align-center>
    <LoginPane :cancellable="true" @success="onSuccess" @cancel="onCancel" />
  </el-dialog>
</template>

<script setup>
import { computed } from "vue"
import { ElDialog } from "element-plus"

import LoginPane from "@/components/auth/LoginPane.vue"
import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

const auth = useAuthStore()
const { t } = useI18n()

const visible = computed({
  get: () => auth.pendingLoginPrompt !== null,
  set: (v) => {
    // Dismissing the dialog (Esc / X) cancels the pending prompt so the
    // awaiting interceptor rejects rather than hanging.
    if (!v) onCancel()
  },
})

function onSuccess() {
  // ``auth.login`` already resolved + cleared the pending prompt; the
  // ``visible`` getter flips false and the dialog closes itself.
}

function onCancel() {
  auth.rejectLoginPrompt()
}
</script>
