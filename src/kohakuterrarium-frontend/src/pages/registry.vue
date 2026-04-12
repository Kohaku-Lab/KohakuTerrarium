<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <h1 class="text-xl font-semibold text-warm-800 dark:text-warm-200 mb-4">Registry</h1>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="Local" name="local">
          <div v-if="loadingLocal" class="py-8 text-center text-secondary">Loading configs...</div>
          <div v-else-if="localConfigs.length === 0" class="card p-8 text-center text-secondary">
            No configs installed.
          </div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ConfigCard
              v-for="cfg in localConfigs"
              :key="cfg.name"
              :config="cfg"
              mode="local"
              @uninstall="handleUninstall"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="Available" name="available">
          <div v-if="loadingRemote" class="py-8 text-center text-secondary">
            Loading available configs...
          </div>
          <div v-else-if="remoteRepos.length === 0" class="card p-8 text-center text-secondary">
            No remote configs available.
          </div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <ConfigCard
              v-for="repo in remoteRepos"
              :key="repo.url || repo.name"
              :config="repo"
              mode="remote"
              :installed="isInstalled(repo.name)"
              :installing="installingSet.has(repo.url)"
              @install="handleInstall"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus"
import { registryAPI } from "@/utils/api"
import ConfigCard from "@/components/registry/ConfigCard.vue"

const activeTab = ref("local")

const localConfigs = ref([])
const remoteRepos = ref([])
const loadingLocal = ref(false)
const loadingRemote = ref(false)
const installingSet = ref(new Set())
const localNames = computed(() => new Set(localConfigs.value.map((c) => c.name)))

function isInstalled(name) {
  return localNames.value.has(name)
}

async function fetchLocal() {
  loadingLocal.value = true
  try {
    localConfigs.value = await registryAPI.listLocal()
  } catch (err) {
    ElMessage.error(`Failed to load local configs: ${err.message}`)
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
    ElMessage.error(`Failed to load remote configs: ${err.message}`)
  } finally {
    loadingRemote.value = false
  }
}

async function handleInstall(repo) {
  const newSet = new Set(installingSet.value)
  newSet.add(repo.url)
  installingSet.value = newSet
  try {
    await registryAPI.install(repo.url, repo.name)
    ElMessage.success(`Installed ${repo.name}`)
    await fetchLocal()
  } catch (err) {
    ElMessage.error(`Install failed: ${err.response?.data?.detail || err.message}`)
  } finally {
    const cleared = new Set(installingSet.value)
    cleared.delete(repo.url)
    installingSet.value = cleared
  }
}

async function handleUninstall(cfg) {
  try {
    await registryAPI.uninstall(cfg.name)
    ElMessage.success(`Uninstalled ${cfg.name}`)
    await fetchLocal()
  } catch (err) {
    ElMessage.error(`Uninstall failed: ${err.response?.data?.detail || err.message}`)
  }
}

onMounted(() => {
  fetchLocal()
  fetchRemote()
})
</script>
