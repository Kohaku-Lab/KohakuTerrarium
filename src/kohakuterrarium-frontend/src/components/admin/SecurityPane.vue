<template>
  <div class="flex flex-col gap-3">
    <p class="text-[12px] text-warm-400">{{ t("admin.security.subtitle") }}</p>

    <div v-if="admin.loadingTokenStatus && !admin.tokenStatus" class="text-[12px] text-warm-400 py-3">
      {{ t("common.loading") }}
    </div>

    <template v-else-if="admin.tokenStatus">
      <!-- Host token (L2) -->
      <div class="card p-4 flex items-center gap-3">
        <span class="i-carbon-network-2 text-iolite text-xl shrink-0" aria-hidden="true" />
        <div class="flex-1 min-w-0">
          <div class="font-medium text-warm-700 dark:text-warm-200">{{ t("admin.security.hostToken") }}</div>
          <div class="text-[12px] text-warm-400">{{ statusLine(admin.tokenStatus.host_token) }}</div>
        </div>
        <el-button size="small" plain @click="rotate('host')">{{ t("admin.security.rotateHost") }}</el-button>
      </div>

      <!-- Admin token (L3) -->
      <div class="card p-4 flex items-center gap-3">
        <span class="i-carbon-rule text-iolite text-xl shrink-0" aria-hidden="true" />
        <div class="flex-1 min-w-0">
          <div class="font-medium text-warm-700 dark:text-warm-200">{{ t("admin.security.adminToken") }}</div>
          <div class="text-[12px] text-warm-400">{{ statusLine(admin.tokenStatus.admin_token) }}</div>
        </div>
        <el-button size="small" plain @click="rotate('admin')">{{ t("admin.security.rotateAdmin") }}</el-button>
      </div>

      <!-- One-time new-token display -->
      <div v-if="rotated" class="card p-3 border-l-3 border-l-amber flex flex-col gap-2">
        <div class="text-[12px] font-medium text-warm-700 dark:text-warm-200">
          {{ t("admin.security.newTokenTitle") }}
        </div>
        <div class="flex items-center gap-2">
          <code class="flex-1 min-w-0 truncate text-[12px] bg-warm-100 dark:bg-warm-800 rounded px-2 py-1">{{ rotated }}</code>
          <el-button size="small" @click="copy(rotated)">
            <span class="i-carbon-copy mr-1" />
            {{ t("admin.security.copy") }}
          </el-button>
        </div>
        <p class="text-[11px] text-warm-400 italic">{{ t("admin.security.newTokenHint") }}</p>
      </div>
    </template>

    <div v-else class="card p-4 text-[12px] text-coral">{{ loadError }}</div>
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElButton, ElMessage, ElMessageBox } from "element-plus"

import { useAdminStore } from "@/stores/admin"
import { useI18n } from "@/utils/i18n"

const admin = useAdminStore()
const { t } = useI18n()

const rotated = ref("")
const loadError = ref("")

function statusLine(entry) {
  if (!entry || !entry.enabled) return t("admin.security.disabled")
  return entry.tail ? t("admin.security.tail", { tail: entry.tail }) : t("admin.security.enabled")
}

async function rotate(which) {
  const confirmMsg = which === "host" ? t("admin.security.rotateHostConfirm") : t("admin.security.rotateAdminConfirm")
  const confirmLabel = which === "host" ? t("admin.security.rotateHost") : t("admin.security.rotateAdmin")
  try {
    await ElMessageBox.confirm(confirmMsg, confirmLabel, {
      type: "warning",
      confirmButtonText: confirmLabel,
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return
  }
  try {
    const res = which === "host" ? await admin.rotateHostToken() : await admin.rotateAdminToken()
    rotated.value = res.token || ""
    ElMessage.success(t("admin.security.rotated"))
  } catch (e) {
    ElMessage.error(_errText(e))
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t("auth.account.copied"))
  } catch {
    ElMessage.warning(t("auth.account.copyFailed"))
  }
}

async function load() {
  loadError.value = ""
  try {
    await admin.fetchTokenStatus()
  } catch (e) {
    loadError.value = _errText(e)
  }
}

function _errText(e) {
  const detail = e?.response?.data?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") return detail.message || detail.error || JSON.stringify(detail)
  return e?.message || String(e)
}

load()
</script>
