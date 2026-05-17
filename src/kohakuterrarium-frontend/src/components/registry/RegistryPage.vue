<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <div class="flex items-start justify-between mb-4 gap-2 flex-wrap">
        <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200">{{ t("common.registry") }}</h1>
        <div class="flex gap-2">
          <el-button size="small" plain @click="installUrlOpen = true">
            <span class="i-carbon-add mr-1" />
            {{ t("registry.installFromUrl") }}
          </el-button>
          <el-button size="small" type="primary" :loading="updatingAll" plain @click="onUpdateAll">
            <span class="i-carbon-renew mr-1" />
            {{ t("registry.updateAll") }}
          </el-button>
        </div>
      </div>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('common.local')" name="local">
          <div v-if="loadingLocal" class="py-8 text-center text-secondary">{{ t("registry.loadingConfigs") }}</div>
          <div v-else-if="localConfigs.length === 0" class="card p-8 text-center text-secondary">{{ t("registry.noConfigsInstalled") }}</div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ConfigCard v-for="cfg in localConfigs" :key="cfg.name" :config="cfg" mode="local" :updating="updatingSet.has(cfg.name)" @uninstall="handleUninstall" @update="handleUpdate" @info="onShowInfo" @edit-files="onEditFiles" />
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

    <InstallFromURLModal v-model="installUrlOpen" @installed="onUrlInstalled" />
    <PackageInfoDrawer v-model="infoOpen" :package-name="infoTarget" />
    <PackageFilesDrawer v-model="filesOpen" :package-name="filesTarget" />
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus"

import ConfigCard from "@/components/registry/ConfigCard.vue"
import InstallFromURLModal from "@/components/registry/InstallFromURLModal.vue"
import PackageInfoDrawer from "@/components/registry/PackageInfoDrawer.vue"
import PackageFilesDrawer from "@/components/registry/PackageFilesDrawer.vue"
import { useI18n } from "@/utils/i18n"
import { registryAPI } from "@/utils/api"

const activeTab = ref("local")
const { t } = useI18n()

const localConfigs = ref([])
const remoteRepos = ref([])
const loadingLocal = ref(false)
const loadingRemote = ref(false)
const installingSet = ref(new Set())
const updatingSet = ref(new Set())
const updatingAll = ref(false)
const installUrlOpen = ref(false)
const infoOpen = ref(false)
const infoTarget = ref("")
const filesOpen = ref(false)
const filesTarget = ref("")
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

async function handleUpdate(config) {
  const next = new Set(updatingSet.value)
  next.add(config.name)
  updatingSet.value = next
  try {
    const r = await registryAPI.update(config.name)
    ElMessage.success(t("registry.updated", { name: config.name, message: r.message }))
    await fetchLocal()
  } catch (err) {
    ElMessage.error(t("registry.updateFailed", { name: config.name, message: err.response?.data?.detail || err.message }))
  } finally {
    const cleared = new Set(updatingSet.value)
    cleared.delete(config.name)
    updatingSet.value = cleared
  }
}

async function onUpdateAll() {
  updatingAll.value = true
  try {
    const r = await registryAPI.updateAll()
    const lines = (r.messages || []).join("\n")
    await ElMessageBox.alert(lines || t("registry.updateAllNoneInstalled"), t("registry.updateAllTitle", { updated: r.updated, skipped: r.skipped }), {
      confirmButtonText: t("common.close"),
    }).catch(() => {})
    await fetchLocal()
  } catch (err) {
    ElMessage.error(t("registry.updateAllFailed", { message: err.response?.data?.detail || err.message }))
  } finally {
    updatingAll.value = false
  }
}

function onUrlInstalled(name) {
  ElMessage.success(t("registry.installedMessage", { name }))
  fetchLocal()
}

function onShowInfo(config) {
  infoTarget.value = config.name
  infoOpen.value = true
}

function onEditFiles(config) {
  filesTarget.value = config.name
  filesOpen.value = true
}

onMounted(() => {
  fetchLocal()
  fetchRemote()
})
</script>
