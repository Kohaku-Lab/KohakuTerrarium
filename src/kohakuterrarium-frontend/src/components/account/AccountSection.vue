<template>
  <div class="flex flex-col gap-4">
    <!-- Identity card -->
    <div class="card p-4 flex items-center gap-3">
      <span class="i-carbon-user-avatar text-iolite text-2xl shrink-0" aria-hidden="true" />
      <div class="flex-1 min-w-0">
        <div class="font-medium text-warm-700 dark:text-warm-200 truncate">
          {{ t("auth.account.signedInAs", { username: user?.username || "" }) }}
        </div>
        <div class="text-[12px] text-warm-400 flex items-center gap-2 flex-wrap">
          <el-tag size="small" :type="user?.role === 'admin' ? 'warning' : 'info'" effect="plain">
            {{ user?.role || "user" }}
          </el-tag>
          <span>{{ t("auth.account.lastLogin") }}: {{ formatTs(user?.last_login_at) }}</span>
        </div>
      </div>
      <el-button size="small" plain @click="onLogout">
        <span class="i-carbon-logout mr-1" />
        {{ t("auth.user.logout") }}
      </el-button>
    </div>

    <!-- Change password -->
    <div class="card p-4 flex flex-col gap-2">
      <h4 class="text-[13px] font-medium text-warm-700 dark:text-warm-200">
        {{ t("auth.account.changePassword") }}
      </h4>
      <div class="flex flex-col gap-2 max-w-sm">
        <input v-model="pw.current" type="password" autocomplete="current-password" class="input-field" :placeholder="t('auth.account.currentPassword')" />
        <input v-model="pw.next" type="password" autocomplete="new-password" class="input-field" :placeholder="t('auth.account.newPassword')" />
        <input v-model="pw.confirm" type="password" autocomplete="new-password" class="input-field" :placeholder="t('auth.account.confirmPassword')" @keydown.enter="onChangePassword" />
        <p v-if="pwError" class="text-[12px] text-coral">{{ pwError }}</p>
        <div>
          <el-button size="small" type="primary" :loading="pwBusy" :disabled="!pwValid" @click="onChangePassword">
            {{ t("auth.account.changePassword") }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- API tokens -->
    <div class="card p-4 flex flex-col gap-3">
      <div class="flex items-center justify-between gap-2">
        <h4 class="text-[13px] font-medium text-warm-700 dark:text-warm-200">
          {{ t("auth.account.tokens") }}
        </h4>
        <el-button size="small" plain @click="loadTokens">
          <span class="i-carbon-renew mr-1" />
          {{ t("common.refresh") }}
        </el-button>
      </div>
      <p class="text-[12px] text-warm-400">{{ t("auth.account.tokensHint") }}</p>

      <!-- Create -->
      <div class="flex items-center gap-2 max-w-md">
        <input v-model="newName" type="text" autocomplete="off" class="input-field font-mono" :placeholder="t('auth.account.tokenNamePlaceholder')" @keydown.enter="onCreateToken" />
        <el-button size="small" type="primary" :loading="createBusy" :disabled="!newName.trim()" @click="onCreateToken">
          <span class="i-carbon-add mr-1" />
          {{ t("auth.account.createToken") }}
        </el-button>
      </div>

      <!-- One-time plaintext display -->
      <div v-if="createdToken" class="card p-3 border-l-3 border-l-amber flex flex-col gap-2">
        <div class="text-[12px] font-medium text-warm-700 dark:text-warm-200">
          {{ t("auth.account.newTokenTitle") }}
        </div>
        <div class="flex items-center gap-2">
          <code class="flex-1 min-w-0 truncate text-[12px] bg-warm-100 dark:bg-warm-800 rounded px-2 py-1">{{ createdToken }}</code>
          <el-button size="small" @click="copy(createdToken)">
            <span class="i-carbon-copy mr-1" />
            {{ t("auth.account.copy") }}
          </el-button>
        </div>
        <p class="text-[11px] text-warm-400 italic">{{ t("auth.account.newTokenHint") }}</p>
      </div>

      <!-- List -->
      <div v-if="loading" class="text-[12px] text-warm-400 py-2">{{ t("common.loading") }}</div>
      <div v-else-if="!tokens.length" class="text-[12px] text-warm-400 italic py-2">
        {{ t("auth.account.noTokens") }}
      </div>
      <ul v-else class="flex flex-col gap-1">
        <li v-for="tok in tokens" :key="tok.id" class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-warm-100 dark:hover:bg-warm-800">
          <span class="i-carbon-api text-warm-400 shrink-0" aria-hidden="true" />
          <span class="flex-1 min-w-0">
            <span class="block truncate text-warm-700 dark:text-warm-300 text-[13px]">{{ tok.name }}</span>
            <span class="block text-[11px] text-warm-400"> {{ t("auth.account.created") }}: {{ formatTs(tok.created_at) }} · {{ t("auth.account.lastUsed") }}: {{ formatTs(tok.last_used_at) }} </span>
          </span>
          <el-button size="small" text type="danger" @click="onRevoke(tok)">
            {{ t("auth.account.revoke") }}
          </el-button>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElMessage, ElMessageBox, ElTag } from "element-plus"

import { authApi } from "@/utils/authApi"
import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

const auth = useAuthStore()
const { t } = useI18n()

const user = computed(() => auth.currentUser)

// ── Change password ────────────────────────────────────────────────
const pw = ref({ current: "", next: "", confirm: "" })
const pwError = ref("")
const pwBusy = ref(false)
const pwValid = computed(() => pw.value.current && pw.value.next && pw.value.confirm)

async function onChangePassword() {
  if (!pwValid.value || pwBusy.value) return
  pwError.value = ""
  if (pw.value.next !== pw.value.confirm) {
    pwError.value = t("auth.account.passwordMismatch")
    return
  }
  pwBusy.value = true
  try {
    await authApi.changePassword({
      currentPassword: pw.value.current,
      newPassword: pw.value.next,
    })
    pw.value = { current: "", next: "", confirm: "" }
    ElMessage.success(t("auth.account.passwordChanged"))
  } catch (e) {
    if (e?.response?.status === 401) {
      pwError.value = t("auth.account.passwordWrong")
    } else {
      pwError.value = t("auth.account.passwordFailed", { error: _errText(e) })
    }
  } finally {
    pwBusy.value = false
  }
}

// ── API tokens ─────────────────────────────────────────────────────
const tokens = ref([])
const loading = ref(false)
const newName = ref("")
const createBusy = ref(false)
const createdToken = ref("")

async function loadTokens() {
  loading.value = true
  try {
    tokens.value = await authApi.listTokens()
  } catch (e) {
    ElMessage.error(_errText(e))
  } finally {
    loading.value = false
  }
}

async function onCreateToken() {
  const name = newName.value.trim()
  if (!name || createBusy.value) return
  createBusy.value = true
  try {
    const res = await authApi.createToken(name)
    createdToken.value = res.token || ""
    newName.value = ""
    await loadTokens()
  } catch (e) {
    ElMessage.error(_errText(e))
  } finally {
    createBusy.value = false
  }
}

async function onRevoke(tok) {
  try {
    await ElMessageBox.confirm(t("auth.account.revokeConfirm", { name: tok.name }), t("auth.account.revoke"), { type: "warning", confirmButtonText: t("auth.account.revoke"), cancelButtonText: t("common.cancel") })
  } catch {
    return
  }
  try {
    await authApi.revokeToken(tok.id)
    // The just-revoked token can't be the one shown for copy, but clear
    // a stale one-time display defensively when the list changes.
    if (tokens.value.length <= 1) createdToken.value = ""
    await loadTokens()
    ElMessage.success(t("auth.account.revoked"))
  } catch (e) {
    ElMessage.error(_errText(e))
  }
}

async function onLogout() {
  await auth.logout()
}

// ── helpers ────────────────────────────────────────────────────────
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t("auth.account.copied"))
  } catch {
    ElMessage.warning(t("auth.account.copyFailed"))
  }
}

function formatTs(value) {
  if (!value) return t("auth.account.never")
  try {
    return new Date(value).toLocaleString()
  } catch {
    return String(value)
  }
}

function _errText(e) {
  const detail = e?.response?.data?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") return detail.message || detail.error || JSON.stringify(detail)
  return e?.message || String(e)
}

loadTokens()
</script>
