<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <!-- Header -->
      <div class="mb-8">
        <h1 class="text-2xl font-bold text-warm-800 dark:text-warm-200 mb-1">KohakuTerrarium</h1>
        <p class="text-secondary">
          Build agents that work alone. Compose them into teams that work together.
        </p>
      </div>

      <!-- Stats cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div v-for="stat in stats" :key="stat.label" class="card p-4">
          <div class="text-2xl font-bold mb-1" :class="stat.color">
            {{ stat.value }}
          </div>
          <div class="text-secondary">{{ stat.label }}</div>
        </div>
      </div>

      <!-- Running instances -->
      <div class="mb-8">
        <h2 class="section-title">Running Instances</h2>
        <div v-if="instances.running.length === 0" class="card p-8 text-center text-secondary">
          No instances running. Start one to get going.
        </div>
        <div v-else class="flex flex-col gap-3">
          <div
            v-for="inst in instances.running"
            :key="inst.id"
            class="card-hover p-4 flex items-center gap-4"
            @click="$router.push(`/instances/${inst.id}`)"
          >
            <StatusDot :status="inst.status" />
            <div class="flex-1 min-w-0">
              <div class="font-medium text-warm-800 dark:text-warm-200">
                {{ inst.config_name }}
              </div>
              <div class="text-secondary truncate">
                {{ inst.pwd }}
              </div>
            </div>
            <GemBadge :gem="inst.type === 'terrarium' ? 'iolite' : 'aquamarine'">
              {{ inst.type }}
            </GemBadge>
            <div class="text-secondary text-xs">
              {{ inst.creatures.length }} creature{{ inst.creatures.length !== 1 ? "s" : "" }}
            </div>
            <button
              class="btn-icon text-coral hover:text-coral-dark flex-shrink-0"
              title="Stop instance"
              @click.stop="handleStop(inst)"
            >
              <span class="i-carbon-stop-filled" />
            </button>
          </div>
        </div>
      </div>

      <!-- Quick start -->
      <div>
        <h2 class="section-title">Quick Start</h2>
        <div class="flex flex-wrap gap-3">
          <button class="btn-primary" @click="$router.push('/new')">
            <span class="i-carbon-add mr-1" /> Start New Instance
          </button>
        </div>
      </div>
    </div>

    <!-- Stop confirmation dialog -->
    <el-dialog
      v-model="showStopConfirm"
      title="Stop Instance"
      width="400px"
      :close-on-click-modal="true"
    >
      <p class="text-warm-600 dark:text-warm-300">
        Stop <strong>{{ stopTarget?.config_name }}</strong
        >? This will terminate the {{ stopTarget?.type }} and all its processes.
      </p>
      <template #footer>
        <el-button size="small" @click="showStopConfirm = false">Cancel</el-button>
        <el-button size="small" type="danger" :loading="stopping" @click="confirmStop"
          >Stop</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus"
import StatusDot from "@/components/common/StatusDot.vue"
import GemBadge from "@/components/common/GemBadge.vue"
import { useInstancesStore } from "@/stores/instances"

const instances = useInstancesStore()
instances.fetchAll()

// Start auto-refresh polling
instances.startPolling()
onUnmounted(() => {
  instances.stopPolling()
})

const showStopConfirm = ref(false)
const stopTarget = ref(null)
const stopping = ref(false)

const stats = computed(() => [
  {
    label: "Running",
    value: instances.running.length,
    color: "text-aquamarine",
  },
  {
    label: "Terrariums",
    value: instances.terrariums.length,
    color: "text-iolite dark:text-iolite-light",
  },
  {
    label: "Creatures",
    value: instances.list.reduce((acc, i) => acc + i.creatures.length, 0),
    color: "text-taaffeite dark:text-taaffeite-light",
  },
  {
    label: "Channels",
    value: instances.list.reduce((acc, i) => acc + i.channels.length, 0),
    color: "text-amber",
  },
])

function handleStop(inst) {
  stopTarget.value = inst
  showStopConfirm.value = true
}

async function confirmStop() {
  if (!stopTarget.value) return
  stopping.value = true
  try {
    await instances.stop(stopTarget.value.id)
    ElMessage({
      message: `Stopped ${stopTarget.value.config_name}`,
      type: "success",
    })
    showStopConfirm.value = false
  } catch (err) {
    ElMessage({
      message: `Failed to stop: ${err.message || "Unknown error"}`,
      type: "error",
    })
  } finally {
    stopping.value = false
  }
}
</script>
