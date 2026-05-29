<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-5xl">
      <div class="mb-4">
        <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200">{{ t("admin.portal.title") }}</h1>
        <p class="text-secondary text-sm">{{ t("admin.portal.subtitle") }}</p>
      </div>

      <!-- Role gate (defense-in-depth — the launcher is already hidden
           for non-admins, and the backend 403s regardless). -->
      <div v-if="!auth.isAdmin" class="card p-8 text-center text-secondary">
        {{ t("admin.portal.notAuthorized") }}
      </div>

      <el-tabs v-else v-model="activeTab">
        <el-tab-pane :label="t('admin.portal.tabs.users')" name="users">
          <UsersPane />
        </el-tab-pane>
        <el-tab-pane :label="t('admin.portal.tabs.invitations')" name="invitations">
          <InvitationsPane />
        </el-tab-pane>
        <el-tab-pane :label="t('admin.portal.tabs.security')" name="security">
          <SecurityPane />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElTabPane, ElTabs } from "element-plus"

import InvitationsPane from "@/components/admin/InvitationsPane.vue"
import SecurityPane from "@/components/admin/SecurityPane.vue"
import UsersPane from "@/components/admin/UsersPane.vue"
import { useAuthStore } from "@/stores/auth"
import { useI18n } from "@/utils/i18n"

// Accept the tab spec TabContent passes so the registry mount is clean,
// even though this tab needs no per-tab params.
defineProps({ tab: { type: Object, default: null } })

const auth = useAuthStore()
const { t } = useI18n()

const activeTab = ref("users")
</script>
