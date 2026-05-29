<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between gap-2">
      <p class="text-[12px] text-warm-400">{{ t("admin.invitations.subtitle") }}</p>
      <div class="flex items-center gap-2">
        <el-button size="small" plain @click="reload">
          <span class="i-carbon-renew mr-1" />
          {{ t("common.refresh") }}
        </el-button>
        <el-button size="small" type="primary" @click="openCreate">
          <span class="i-carbon-add mr-1" />
          {{ t("admin.invitations.create") }}
        </el-button>
      </div>
    </div>

    <!-- One-time token display -->
    <div v-if="createdToken" class="card p-3 border-l-3 border-l-amber flex flex-col gap-2">
      <div class="text-[12px] font-medium text-warm-700 dark:text-warm-200">
        {{ t("admin.invitations.newTitle") }}
      </div>
      <div class="flex items-center gap-2">
        <code class="flex-1 min-w-0 truncate text-[12px] bg-warm-100 dark:bg-warm-800 rounded px-2 py-1">{{ createdToken }}</code>
        <el-button size="small" @click="copy(createdToken)">
          <span class="i-carbon-copy mr-1" />
          {{ t("admin.invitations.copyToken") }}
        </el-button>
      </div>
      <p class="text-[11px] text-warm-400 italic">{{ t("admin.invitations.linkHint") }}</p>
    </div>

    <div v-if="admin.loadingInvitations" class="text-[12px] text-warm-400 py-3">{{ t("common.loading") }}</div>
    <div v-else-if="!admin.invitations.length" class="text-[12px] text-warm-400 italic py-3">
      {{ t("admin.invitations.none") }}
    </div>
    <el-table v-else :data="admin.invitations" size="small" stripe>
      <el-table-column :label="t('admin.invitations.id')" width="70">
        <template #default="{ row }">
          <span class="text-[12px] text-warm-400">#{{ row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.invitations.role')" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.role === 'admin' ? 'warning' : 'info'" effect="plain">{{ row.role }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.invitations.expiresAt')" min-width="170">
        <template #default="{ row }">
          <span class="text-[12px] text-warm-400">{{ row.expires_at ? formatTs(row.expires_at) : t("admin.invitations.noExpiry") }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.invitations.created')" min-width="170">
        <template #default="{ row }">
          <span class="text-[12px] text-warm-400">{{ formatTs(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column width="90" align="right">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="onRevoke(row)">
            {{ t("admin.invitations.revoke") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Create dialog -->
    <el-dialog v-model="createOpen" :title="t('admin.invitations.create')" width="420px" align-center>
      <div class="flex flex-col gap-2 text-[13px]">
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("admin.invitations.role") }}</label>
          <el-select v-model="form.role" size="small" class="w-full">
            <el-option value="user" label="user" />
            <el-option value="admin" label="admin" />
          </el-select>
        </div>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">
            {{ t("admin.invitations.expiresInHours") }}
            <span class="text-warm-400 font-normal">{{ t("admin.invitations.expiresOptional") }}</span>
          </label>
          <input v-model="form.expiresInHours" type="number" min="1" class="input-field font-mono" />
        </div>
        <p v-if="createError" class="text-[12px] text-coral">{{ createError }}</p>
      </div>
      <template #footer>
        <el-button size="small" @click="createOpen = false">{{ t("common.cancel") }}</el-button>
        <el-button size="small" type="primary" :loading="createBusy" @click="onCreate">
          {{ t("admin.invitations.create") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElButton, ElDialog, ElMessage, ElMessageBox, ElOption, ElSelect, ElTable, ElTableColumn, ElTag } from "element-plus"

import { useAdminStore } from "@/stores/admin"
import { useI18n } from "@/utils/i18n"

const admin = useAdminStore()
const { t } = useI18n()

const createOpen = ref(false)
const createBusy = ref(false)
const createError = ref("")
const form = ref({ role: "user", expiresInHours: "" })
const createdToken = ref("")

function openCreate() {
  form.value = { role: "user", expiresInHours: "" }
  createError.value = ""
  createOpen.value = true
}

async function onCreate() {
  if (createBusy.value) return
  createBusy.value = true
  createError.value = ""
  try {
    const hours = form.value.expiresInHours === "" ? null : Number(form.value.expiresInHours)
    const res = await admin.createInvitation({ role: form.value.role, expiresInHours: hours })
    createdToken.value = res.token || ""
    createOpen.value = false
    ElMessage.success(t("admin.invitations.createdOk"))
  } catch (e) {
    createError.value = _errText(e)
  } finally {
    createBusy.value = false
  }
}

async function onRevoke(row) {
  try {
    await ElMessageBox.confirm(t("admin.invitations.revokeConfirm"), t("admin.invitations.revoke"), {
      type: "warning",
      confirmButtonText: t("admin.invitations.revoke"),
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return
  }
  try {
    await admin.revokeInvitation(row.id)
    createdToken.value = ""
    ElMessage.success(t("admin.invitations.revoked"))
  } catch (e) {
    ElMessage.error(_errText(e))
  }
}

async function reload() {
  try {
    await admin.fetchInvitations()
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

function formatTs(value) {
  if (!value) return ""
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

reload()
</script>
