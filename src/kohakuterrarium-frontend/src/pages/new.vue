<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-3xl">
      <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 mb-6">Start New Instance</h1>

      <!-- Working directory -->
      <div class="card p-5 mb-6">
        <label class="block text-sm font-medium text-warm-700 dark:text-warm-300 mb-2">
          Working Directory
        </label>
        <div class="flex gap-2">
          <input
            v-model="pwd"
            type="text"
            class="input-field flex-1 font-mono"
            placeholder="/home/user/my-project"
          />
        </div>
        <p class="text-xs text-warm-400 mt-2">
          The creature/terrarium will run with this as its working directory.
        </p>
      </div>

      <!-- Type selection -->
      <div class="flex gap-3 mb-6">
        <button
          v-for="t in types"
          :key="t.key"
          class="flex-1 card p-4 text-center transition-all"
          :class="
            selectedType === t.key
              ? 'border-iolite dark:border-iolite-light ring-1 ring-iolite/20'
              : 'hover:border-warm-300 dark:hover:border-warm-600 cursor-pointer'
          "
          @click="selectedType = t.key"
        >
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
        <div v-if="availableConfigs.length === 0" class="text-secondary text-sm py-4 text-center">
          No {{ selectedType }} configs found.
        </div>
        <div v-else class="flex flex-col gap-1">
          <div
            v-for="config in availableConfigs"
            :key="config.path"
            class="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors"
            :class="
              selectedConfig === config.path
                ? 'bg-iolite/10 dark:bg-iolite/15'
                : 'hover:bg-warm-50 dark:hover:bg-warm-800'
            "
            @click="selectedConfig = config.path"
          >
            <div
              class="w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors"
              :class="
                selectedConfig === config.path
                  ? 'border-iolite dark:border-iolite-light'
                  : 'border-warm-300 dark:border-warm-600'
              "
            >
              <div
                v-if="selectedConfig === config.path"
                class="w-2.5 h-2.5 rounded-full bg-iolite dark:bg-iolite-light"
              />
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
        <button class="btn-secondary" @click="$router.push('/')">Cancel</button>
        <button
          class="btn-primary"
          :disabled="!canStart"
          :class="{ 'opacity-50 cursor-not-allowed': !canStart }"
          @click="startInstance"
        >
          <span class="i-carbon-play mr-1" /> Start Instance
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { configAPI } from "@/utils/api"
import { useConfigsStore } from "@/stores/configs"
import { useInstancesStore } from "@/stores/instances"
import { ElMessage } from "element-plus"

const router = useRouter()
const configs = useConfigsStore()
const instances = useInstancesStore()
configs.fetchAll()

const pwd = ref("")

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

async function startInstance() {
  if (!canStart.value) return
  starting.value = true
  try {
    const id = await instances.create(selectedType.value, selectedConfig.value, pwd.value)
    ElMessage.success(`Started ${selectedType.value}`)
    router.push(`/instances/${id}`)
  } catch (err) {
    ElMessage.error(`Failed to start: ${err.response?.data?.detail || err.message}`)
  } finally {
    starting.value = false
  }
}
</script>
