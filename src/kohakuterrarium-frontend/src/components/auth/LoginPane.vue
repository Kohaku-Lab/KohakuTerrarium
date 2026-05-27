<template>
  <div class="flex flex-col gap-3 text-[13px]">
    <div class="flex items-center gap-2 px-1">
      <span class="i-carbon-user-avatar text-iolite text-lg" aria-hidden="true" />
      <div class="flex-1 min-w-0">
        <h3 class="text-warm-700 dark:text-warm-200 font-medium">
          {{ mode === "register" ? t("auth.login.register") : t("auth.login.title") }}
        </h3>
        <p class="text-[11px] text-warm-400 truncate">
          {{ t("auth.login.hint", { host: activeHostLabel }) }}
        </p>
      </div>
    </div>

    <div class="flex flex-col gap-2">
      <div>
        <label class="text-[11px] text-warm-400 block mb-1">{{ t("auth.login.username") }}</label>
        <input v-model="form.username" type="text" autocomplete="username" class="input-field font-mono" @keydown.enter="onSubmit" />
      </div>
      <div>
        <label class="text-[11px] text-warm-400 block mb-1">{{ t("auth.login.password") }}</label>
        <input v-model="form.password" type="password" :autocomplete="mode === 'register' ? 'new-password' : 'current-password'" class="input-field font-mono" @keydown.enter="onSubmit" />
      </div>
      <div v-if="mode === 'register' && needsInvitation">
        <label class="text-[11px] text-warm-400 block mb-1">
          {{ t("auth.login.invitationToken") }}
        </label>
        <input v-model="form.invitationToken" type="text" autocomplete="off" class="input-field font-mono" :placeholder="t('auth.login.invitationHint')" @keydown.enter="onSubmit" />
        <p class="text-[11px] text-warm-400 italic mt-1">
          {{ t("auth.login.invitationHint") }}
        </p>
      </div>
    </div>

    <p v-if="errorMessage" class="text-[12px] text-coral">{{ errorMessage }}</p>

    <div class="flex items-center justify-between gap-2 mt-1">
      <button v-if="canToggleMode" type="button" class="text-[11px] text-iolite hover:underline" @click="toggleMode">
        {{ mode === "register" ? t("auth.login.toggleToLogin") : t("auth.login.toggleToRegister") }}
      </button>
      <span v-else class="text-[11px] text-warm-400 italic">{{ t("auth.login.registrationClosed") }}</span>
      <div class="flex items-center gap-2">
        <el-button v-if="cancellable" size="small" @click="onCancel">{{ t("auth.login.cancel") }}</el-button>
        <el-button size="small" type="primary" :loading="busy" :disabled="!isFormValid" @click="onSubmit">
          {{ mode === "register" ? t("auth.login.register") : t("auth.login.submit") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton } from "element-plus"

import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  /** Show the cancel button (e.g. inside a modal).  AuthGate (full-
   *  screen blocker) hides it. */
  cancellable: { type: Boolean, default: false },
})
const emit = defineEmits(["success", "cancel"])

const auth = useAuthStore()
const hosts = useHostsStore()
const { t } = useI18n()

const mode = ref("login")
const form = ref({ username: "", password: "", invitationToken: "" })
const errorMessage = ref("")
const busy = ref(false)

const activeHostLabel = computed(() => hosts.activeHost?.name || "")

const needsInvitation = computed(() => auth.registrationMode === "invite_only")

/** Allow the user to flip between login and register modes only when
 *  the host actually accepts self-registration in some form. */
const canToggleMode = computed(() => auth.registrationMode === "open" || auth.registrationMode === "invite_only")

const isFormValid = computed(() => {
  if (!form.value.username.trim() || !form.value.password) return false
  if (mode.value === "register" && needsInvitation.value && !form.value.invitationToken.trim()) {
    return false
  }
  return true
})

function toggleMode() {
  if (!canToggleMode.value) return
  mode.value = mode.value === "login" ? "register" : "login"
  errorMessage.value = ""
}

function _formatError(err) {
  const resp = err?.response
  if (!resp) return t("auth.login.connectionFailed")
  if (resp.status === 401) return t("auth.login.invalidCredentials")
  const detail = resp.data?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") {
    return detail.message || detail.error || JSON.stringify(detail)
  }
  return err?.message || String(err)
}

async function onSubmit() {
  if (!isFormValid.value || busy.value) return
  errorMessage.value = ""
  busy.value = true
  try {
    if (mode.value === "register") {
      await auth.register({
        username: form.value.username.trim(),
        password: form.value.password,
        invitationToken: form.value.invitationToken.trim(),
      })
    } else {
      await auth.login({
        username: form.value.username.trim(),
        password: form.value.password,
      })
    }
    emit("success")
  } catch (e) {
    errorMessage.value = _formatError(e)
  } finally {
    busy.value = false
  }
}

function onCancel() {
  emit("cancel")
}
</script>
