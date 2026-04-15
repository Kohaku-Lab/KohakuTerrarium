<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200 mb-4">{{ t("common.registry") }}</h1>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('common.local')" name="local">
          <div v-if="loadingLocal" class="py-8 text-center text-secondary">{{ t("registry.loadingConfigs") }}</div>
          <div v-else-if="localConfigs.length === 0" class="card p-8 text-center text-secondary">{{ t("registry.noConfigsInstalled") }}</div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ConfigCard v-for="cfg in localConfigs" :key="cfg.name" :config="cfg" mode="local" @uninstall="handleUninstall" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="t('common.available')" name="available">
          <div v-if="loadingRemote" class="py-8 text-center text-secondary">{{ t("registry.loadingAvailable") }}</div>
          <div v-else-if="remoteRepos.length === 0" class="card p-8 text-center text-secondary">{{ t("registry.noRemoteConfigs") }}</div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ConfigCard v-for="repo in remoteRepos" :key="repo.url || repo.name" :config="repo" mode="remote" :installed="isInstalled(repo.name)" :installing="installingSet.has(repo.url)" @install="handleInstall" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus"

import ConfigCard from "@/components/registry/ConfigCard.vue"
import { useI18n } from "@/utils/i18n"
import { registryAPI } from "@/utils/api"

const activeTab = ref("local")
const { t } = useI18n()

const localConfigs = ref([])
const remoteRepos = ref([])
const loadingLocal = ref(false)
const loadingRemote = ref(false)
const installingSet = ref(new Set())
const localNames = computed(() => new Set(localConfigs.value.map((config) => config.name)))

function isInstalled(name) {
  return localNames.value.has(name)
}

async function fetchLocal() {
  loadingLocal.value = true
  try {
    localConfigs.value = await registryAPI.listLocal()
  } catch (err) {
    ElMessage.error(t("registry.loadLocalFailed", { message: err.message }))
  } finally {
    loadingLocal.value = false
  }
}

async function fetchRemote() {
  loadingRemote.value = true
  try {
    const result = await registryAPI.listRemote()
    remoteRepos.value = result.repos || []
  } catch (err) {
    ElMessage.error(t("registry.loadRemoteFailed", { message: err.message }))
  } finally {
    loadingRemote.value = false
  }
}

async function handleInstall(repo) {
  const nextSet = new Set(installingSet.value)
  nextSet.add(repo.url)
  installingSet.value = nextSet
  try {
    await registryAPI.install(repo.url, repo.name)
    ElMessage.success(t("registry.installedMessage", { name: repo.name }))
    await fetchLocal()
  } catch (err) {
    ElMessage.error(t("registry.installFailed", { message: err.response?.data?.detail || err.message }))
  } finally {
    const cleared = new Set(installingSet.value)
    cleared.delete(repo.url)
    installingSet.value = cleared
  }
}

async function handleUninstall(config) {
  try {
    await registryAPI.uninstall(config.name)
    ElMessage.success(t("registry.uninstalledMessage", { name: config.name }))
    await fetchLocal()
  } catch (err) {
    ElMessage.error(t("registry.uninstallFailed", { message: err.response?.data?.detail || err.message }))
  }
}

onMounted(() => {
  fetchLocal()
  fetchRemote()
})
</script>
