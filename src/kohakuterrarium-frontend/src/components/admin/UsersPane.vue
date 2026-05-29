<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between gap-2">
      <p class="text-[12px] text-warm-400">{{ t("admin.users.subtitle") }}</p>
      <div class="flex items-center gap-2">
        <el-button size="small" plain @click="reload">
          <span class="i-carbon-renew mr-1" />
          {{ t("common.refresh") }}
        </el-button>
        <el-button size="small" type="primary" @click="openCreate">
          <span class="i-carbon-add mr-1" />
          {{ t("admin.users.create") }}
        </el-button>
      </div>
    </div>

    <div v-if="admin.loadingUsers" class="text-[12px] text-warm-400 py-3">{{ t("common.loading") }}</div>
    <div v-else-if="!admin.users.length" class="text-[12px] text-warm-400 italic py-3">
      {{ t("admin.users.none") }}
    </div>
    <el-table v-else :data="admin.users" size="small" stripe>
      <el-table-column :label="t('admin.users.username')" min-width="160">
        <template #default="{ row }">
          <span class="font-mono">{{ row.username }}</span>
          <el-tag v-if="row.id === selfId" size="small" effect="plain" class="ml-1">{{ t("admin.users.you") }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.users.role')" width="120">
        <template #default="{ row }">
          <el-select :model-value="row.role" size="small" @change="(v) => onRole(row, v)">
            <el-option value="user" label="user" />
            <el-option value="admin" label="admin" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.users.active')" width="90">
        <template #default="{ row }">
          <el-switch :model-value="row.is_active" @change="(v) => onActive(row, v)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.users.created')" width="170">
        <template #default="{ row }">
          <span class="text-[12px] text-warm-400">{{ formatTs(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.users.lastLogin')" width="170">
        <template #default="{ row }">
          <span class="text-[12px] text-warm-400">{{ formatTs(row.last_login_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('admin.users.actions')" width="90" align="right">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="onDelete(row)">
            {{ t("admin.users.delete") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Create dialog -->
    <el-dialog v-model="createOpen" :title="t('admin.users.create')" width="420px" align-center>
      <div class="flex flex-col gap-2 text-[13px]">
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("admin.users.username") }}</label>
          <input v-model="form.username" type="text" autocomplete="off" class="input-field font-mono" />
        </div>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("admin.users.password") }}</label>
          <input v-model="form.password" type="password" autocomplete="new-password" class="input-field font-mono" />
        </div>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("admin.users.role") }}</label>
          <el-select v-model="form.role" size="small" class="w-full">
            <el-option value="user" label="user" />
            <el-option value="admin" label="admin" />
          </el-select>
        </div>
        <p v-if="createError" class="text-[12px] text-coral">{{ createError }}</p>
      </div>
      <template #footer>
        <el-button size="small" @click="createOpen = false">{{ t("common.cancel") }}</el-button>
        <el-button size="small" type="primary" :loading="createBusy" :disabled="!createValid" @click="onCreate">
          {{ t("admin.users.create") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElDialog, ElMessage, ElMessageBox, ElOption, ElSelect, ElSwitch, ElTable, ElTableColumn, ElTag } from "element-plus"

import { useAdminStore } from "@/stores/admin"
import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

const admin = useAdminStore()
const auth = useAuthStore()
const { t } = useI18n()

const selfId = computed(() => auth.currentUser?.id)

const createOpen = ref(false)
const createBusy = ref(false)
const createError = ref("")
const form = ref({ username: "", password: "", role: "user" })
const createValid = computed(() => form.value.username.trim() && form.value.password)

function openCreate() {
  form.value = { username: "", password: "", role: "user" }
  createError.value = ""
  createOpen.value = true
}

async function onCreate() {
  if (!createValid.value || createBusy.value) return
  createBusy.value = true
  createError.value = ""
  try {
    await admin.createUser({
      username: form.value.username.trim(),
      password: form.value.password,
      role: form.value.role,
    })
    createOpen.value = false
    ElMessage.success(t("admin.users.createdOk"))
  } catch (e) {
    createError.value = _errText(e)
  } finally {
    createBusy.value = false
  }
}

async function onRole(row, role) {
  if (role === row.role) return
  try {
    await admin.patchUser(row.id, { role })
  } catch (e) {
    ElMessage.error(_errText(e))
    await admin.fetchUsers() // revert optimistic select
  }
}

async function onActive(row, isActive) {
  try {
    await admin.patchUser(row.id, { isActive })
  } catch (e) {
    ElMessage.error(_errText(e))
    await admin.fetchUsers() // revert optimistic switch
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(t("admin.users.deleteConfirm", { username: row.username }), t("admin.users.delete"), { type: "warning", confirmButtonText: t("admin.users.delete"), cancelButtonText: t("common.cancel") })
  } catch {
    return
  }
  try {
    await admin.deleteUser(row.id)
    ElMessage.success(t("admin.users.deleted"))
  } catch (e) {
    ElMessage.error(_errText(e))
  }
}

async function reload() {
  try {
    await admin.fetchUsers()
  } catch (e) {
    ElMessage.error(_errText(e))
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

reload()
</script>
