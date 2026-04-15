<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-3xl">
      <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 mb-6">Start New Instance</h1>

      <!-- Working directory -->
      <div class="card p-5 mb-6">
        <label class="block text-sm font-medium text-warm-700 dark:text-warm-300 mb-2"> Working Directory </label>
        <div class="flex gap-2">
          <input v-model="pwd" type="text" class="input-field flex-1 font-mono" placeholder="/home/user/my-project" />
          <button class="btn-secondary shrink-0" :disabled="browseLoading" @click="openPicker">Browse…</button>
        </div>
        <p class="text-xs text-warm-400 mt-2">The creature/terrarium will run with this as its working directory.</p>
      </div>

      <!-- Type selection -->
      <div class="flex gap-3 mb-6">
        <button v-for="t in types" :key="t.key" class="flex-1 card p-4 text-center transition-all" :class="selectedType === t.key ? 'border-iolite dark:border-iolite-light ring-1 ring-iolite/20' : 'hover:border-warm-300 dark:hover:border-warm-600 cursor-pointer'" @click="selectedType = t.key">
          <div :class="t.icon" class="text-2xl mx-auto mb-2" :style="{ color: t.color }" />
          <div class="font-medium text-warm-800 dark:text-warm-200">
            {{ t.label }}
          </div>
          <div class="text-xs text-secondary mt-1">{{ t.desc }}</div>
        </button>
      </div>

      <!-- Config selection -->
      <div class="card p-5 mb-6">
        <h2 class="text-sm font-medium text-warm-700 dark:text-warm-300 mb-3">Available Configs</h2>
        <div v-if="availableConfigs.length === 0" class="text-secondary text-sm py-4 text-center">No {{ selectedType }} configs found.</div>
        <div v-else class="flex flex-col gap-1">
          <div v-for="config in availableConfigs" :key="config.path" class="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors" :class="selectedConfig === config.path ? 'bg-iolite/10 dark:bg-iolite/15' : 'hover:bg-warm-50 dark:hover:bg-warm-800'" @click="selectedConfig = config.path">
            <div class="w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors" :class="selectedConfig === config.path ? 'border-iolite dark:border-iolite-light' : 'border-warm-300 dark:border-warm-600'">
              <div v-if="selectedConfig === config.path" class="w-2.5 h-2.5 rounded-full bg-iolite dark:bg-iolite-light" />
            </div>
            <div class="flex-1">
              <div class="font-medium text-warm-800 dark:text-warm-200 text-sm">
                {{ config.name }}
              </div>
              <div class="text-xs text-secondary">{{ config.description }}</div>
            </div>
            <div class="text-xs text-warm-400 font-mono">
              {{ config.path }}
            </div>
          </div>
        </div>
      </div>

      <!-- Actions -->
      <div class="flex justify-end gap-3">
        <button class="btn-secondary" @click="$router.push(isMobile ? '/mobile' : '/')">Cancel</button>
        <button class="btn-primary" :disabled="!canStart" :class="{ 'opacity-50 cursor-not-allowed': !canStart }" @click="startInstance"><span class="i-carbon-play mr-1" /> Start Instance</button>
      </div>
    </div>
  </div>

  <el-dialog v-model="pickerOpen" title="Choose working directory" width="720px" :close-on-click-modal="true">
    <div class="flex items-center gap-2 mb-3">
      <button class="btn-secondary" :disabled="!browseParent || browseLoading" @click="browseTo(browseParent)"><span class="i-carbon-arrow-up mr-1" /> Up</button>
      <button class="btn-secondary" :disabled="browseLoading" @click="browseTo(null)"><span class="i-carbon-data-base mr-1" /> Roots</button>
      <div class="flex-1 px-3 py-2 rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 font-mono text-xs truncate">
        {{ browseCurrent?.path || "Choose a root directory" }}
      </div>
      <button class="btn-primary" :disabled="!browseCurrent?.path" @click="selectDirectory(browseCurrent?.path)">Use this folder</button>
    </div>

    <div v-if="browseError" class="mb-3 text-sm text-red-500">{{ browseError }}</div>

    <div v-if="!browseCurrent" class="space-y-2">
      <div class="text-xs uppercase tracking-wider text-warm-400">Available roots</div>
      <button v-for="root in browseRoots" :key="root.path" class="w-full text-left px-3 py-2 rounded border border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800" @click="browseTo(root.path)">
        <div class="flex items-center gap-2">
          <span class="i-carbon-folder text-amber" />
          <span class="font-medium text-warm-800 dark:text-warm-200">{{ root.name }}</span>
          <span class="text-xs text-warm-400 font-mono truncate">{{ root.path }}</span>
        </div>
      </button>
    </div>

    <div v-else class="space-y-2 max-h-96 overflow-y-auto">
      <button v-for="dir in browseDirectories" :key="dir.path" class="w-full text-left px-3 py-2 rounded border transition-colors" :class="selectedBrowsePath === dir.path ? 'border-iolite bg-iolite/10 dark:bg-iolite/15' : 'border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800'" @click="selectedBrowsePath = dir.path" @dblclick="browseTo(dir.path)">
        <div class="flex items-center gap-2">
          <span class="i-carbon-folder text-amber" />
          <span class="font-medium text-warm-800 dark:text-warm-200">{{ dir.name }}</span>
          <span class="text-xs text-warm-400 font-mono truncate">{{ dir.path }}</span>
        </div>
      </button>
      <div v-if="!browseLoading && browseDirectories.length === 0" class="text-sm text-secondary py-6 text-center">No subdirectories available here.</div>
    </div>

    <template #footer>
      <div class="flex justify-between gap-3">
        <button class="btn-secondary" @click="pickerOpen = false">Close</button>
        <button class="btn-primary" :disabled="!selectedBrowsePath" @click="selectDirectory(selectedBrowsePath)">Select highlighted folder</button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { configAPI, filesAPI } from "@/utils/api"
import { useConfigsStore } from "@/stores/configs"
import { useInstancesStore } from "@/stores/instances"
import { ElMessage } from "element-plus"

const isMobile = inject("mobileLayout", false)
const router = useRouter()
const configs = useConfigsStore()
const instances = useInstancesStore()
configs.fetchAll()

const pwd = ref("")
const pickerOpen = ref(false)
const browseLoading = ref(false)
const browseError = ref("")
const browseCurrent = ref(null)
const browseParent = ref(null)
const browseRoots = ref([])
const browseDirectories = ref([])
const selectedBrowsePath = ref("")

// Fetch server cwd as default working directory
onMounted(async () => {
  try {
    const info = await configAPI.getServerInfo()
    if (info.cwd && !pwd.value) pwd.value = info.cwd
  } catch {
    /* ignore — user can type manually */
  }
})
const selectedType = ref("creature")
const selectedConfig = ref(null)
const starting = ref(false)

const types = [
  {
    key: "creature",
    label: "Creature",
    desc: "Single agent",
    icon: "i-carbon-bot",
    color: "#4C9989",
  },
  {
    key: "terrarium",
    label: "Terrarium",
    desc: "Multi-agent team",
    icon: "i-carbon-network-4",
    color: "#5A4FCF",
  },
]

const availableConfigs = computed(() => {
  return selectedType.value === "creature" ? configs.creatures : configs.terrariums
})

const canStart = computed(() => {
  return pwd.value.trim() && selectedConfig.value && !starting.value
})

watch(selectedType, () => {
  selectedConfig.value = null
})

async function openPicker() {
  pickerOpen.value = true
  const initialPath = pwd.value.trim()
  if (!initialPath) {
    await browseTo(null)
    return
  }
  const ok = await browseTo(initialPath)
  if (!ok) await browseTo(null)
}

async function browseTo(path = null) {
  browseLoading.value = true
  browseError.value = ""
  try {
    const data = await filesAPI.browseDirectories(path)
    browseCurrent.value = data.current || null
    browseParent.value = data.parent || null
    browseRoots.value = data.roots || []
    browseDirectories.value = data.directories || []
    selectedBrowsePath.value = data.current?.path || ""
    return true
  } catch (err) {
    browseError.value = err?.response?.data?.detail || err?.message || String(err)
    browseCurrent.value = null
    browseParent.value = null
    browseDirectories.value = []
    if (!path) browseRoots.value = []
    return false
  } finally {
    browseLoading.value = false
  }
}

function selectDirectory(path) {
  if (!path) return
  pwd.value = path
  pickerOpen.value = false
}

async function startInstance() {
  if (!canStart.value) return
  starting.value = true
  try {
    const id = await instances.create(selectedType.value, selectedConfig.value, pwd.value)
    const loaded = await instances.fetchOne(id)
    if (!loaded) throw new Error("Instance did not become available")
    ElMessage.success(`Started ${selectedType.value}`)
    router.replace(isMobile ? `/mobile/${id}` : `/instances/${id}`)
  } catch (err) {
    ElMessage.error(`Failed to start: ${err.response?.data?.detail || err.message}`)
  } finally {
    starting.value = false
  }
}
</script>
