<template>
  <div class="settings-pane flex flex-col gap-3 max-w-3xl">
    <div class="flex items-center gap-2">
      <p class="text-xs text-warm-400 mb-0 flex-1">
        {{ t("cluster.popover.title") }}
      </p>
      <button class="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite" @click="spawnWizardOpen = true">
        <span class="i-carbon-add text-[11px]" />
        {{ t("cluster.settings.spawnClient") }}
      </button>
      <button class="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite" :disabled="rotating" @click="onRotateToken">
        <span class="i-carbon-renew text-[11px]" :class="rotating ? 'animate-spin' : ''" />
        {{ t("cluster.settings.rotateToken") }}
      </button>
      <button class="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite" :disabled="refreshing" @click="refresh">
        <span class="i-carbon-refresh text-[11px]" :class="refreshing ? 'animate-spin' : ''" />
        {{ t("cluster.settings.refresh") }}
      </button>
    </div>

    <div v-if="cluster.sites.length === 0" class="card p-4 text-center text-warm-400 italic">
      {{ t("cluster.settings.empty") }}
    </div>

    <table v-else class="w-full text-sm border-collapse">
      <thead>
        <tr class="text-[11px] uppercase tracking-wider text-warm-500 border-b border-warm-200 dark:border-warm-700">
          <th class="text-left py-2 px-3">{{ t("cluster.settings.headers.site") }}</th>
          <th class="text-left py-2 px-3">{{ t("cluster.settings.headers.status") }}</th>
          <th class="text-right py-2 px-3">{{ t("cluster.settings.headers.creatures") }}</th>
          <th class="text-right py-2 px-3">{{ t("cluster.settings.headers.actions") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="site in cluster.sites" :key="site.nodeId" class="border-b border-warm-100 dark:border-warm-800 hover:bg-warm-100/50 dark:hover:bg-warm-800/30">
          <td class="py-2 px-3 flex items-center gap-2">
            <SiteChip :node-id="site.nodeId" always-show />
            <span class="text-warm-700 dark:text-warm-200 font-mono text-xs">{{ site.nodeId }}</span>
          </td>
          <td class="py-2 px-3">
            <span class="inline-flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full" :class="dotClass(site)" />
              <span :class="statusClass(site)">{{ site.status }}</span>
            </span>
          </td>
          <td class="py-2 px-3 text-right text-warm-600 dark:text-warm-300">
            {{ site.creatures !== null ? site.creatures : "—" }}
          </td>
          <td class="py-2 px-3 text-right">
            <el-dropdown v-if="site.nodeId !== '_host'" trigger="click" @command="(c) => onRowCommand(c, site)">
              <button class="text-[11px] px-2 py-0.5 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite">
                <span class="i-carbon-overflow-menu-horizontal" />
              </button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="disconnect">
                    <span class="i-carbon-logout mr-1" />
                    {{ t("cluster.settings.disconnect") }}
                  </el-dropdown-item>
                  <el-dropdown-item command="block" :class="'text-coral'">
                    <span class="i-carbon-block-storage mr-1" />
                    {{ t("cluster.settings.block") }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="cluster.error" class="text-coral text-xs">{{ cluster.error }}</div>

    <SpawnClientWizard v-model="spawnWizardOpen" />
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import SiteChip from "@/components/cluster/SiteChip.vue"
import SpawnClientWizard from "@/components/settings/lab/SpawnClientWizard.vue"
import { labAPI } from "@/utils/api"
import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

const cluster = useClusterStore()
const { t } = useI18n()
const refreshing = ref(false)
const rotating = ref(false)
const spawnWizardOpen = ref(false)

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await cluster.hydrate()
  } finally {
    refreshing.value = false
  }
}

async function onRowCommand(cmd, site) {
  if (cmd === "disconnect") {
    try {
      await ElMessageBox.confirm(t("cluster.settings.disconnectConfirm", { node: site.nodeId }), {
        confirmButtonText: t("cluster.settings.disconnect"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      })
    } catch {
      return
    }
    try {
      await labAPI.disconnectClient(site.nodeId)
      ElMessage.success(t("cluster.settings.disconnected", { node: site.nodeId }))
      await cluster.hydrate()
    } catch (e) {
      ElMessage.error(e?.response?.data?.detail || e.message || "disconnect failed")
    }
  } else if (cmd === "block") {
    try {
      await ElMessageBox.confirm(t("cluster.settings.blockConfirm", { node: site.nodeId }), {
        confirmButtonText: t("cluster.settings.block"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      })
    } catch {
      return
    }
    try {
      await labAPI.blockClient(site.nodeId, "operator-block")
      ElMessage.success(t("cluster.settings.blocked", { node: site.nodeId }))
      await cluster.hydrate()
    } catch (e) {
      ElMessage.error(e?.response?.data?.detail || e.message || "block failed")
    }
  }
}

async function onRotateToken() {
  try {
    await ElMessageBox.confirm(t("cluster.settings.rotateConfirm"), {
      confirmButtonText: t("cluster.settings.rotateToken"),
      cancelButtonText: t("common.cancel"),
      type: "warning",
    })
  } catch {
    return
  }
  rotating.value = true
  try {
    const r = await labAPI.rotatePairingToken()
    ElMessage.success(t("cluster.settings.tokenRotated"))
    spawnWizardOpen.value = true
    // Store the new token on the cluster store so the wizard can show it.
    cluster.setLatestToken?.(r.token)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || "rotate failed")
  } finally {
    rotating.value = false
  }
}

function dotClass(site) {
  if (site.status === "online") return "bg-emerald"
  if (site.status === "unreachable") return "bg-rose"
  return "bg-warm-400"
}

function statusClass(site) {
  if (site.status === "online") return "text-emerald-dark dark:text-emerald-light"
  if (site.status === "unreachable") return "text-rose"
  return "text-warm-500"
}
</script>
