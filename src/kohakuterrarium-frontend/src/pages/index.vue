<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page">
      <!-- Header -->
      <div class="mb-8">
        <h1 class="text-2xl font-bold text-warm-800 dark:text-warm-200 mb-1">KohakuTerrarium</h1>
        <p class="text-secondary">{{ t("home.subtitle") }}</p>
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
        <h2 class="section-title">{{ t("home.runningInstances") }}</h2>
        <div v-if="instances.running.length === 0" class="card p-8 text-center text-secondary">{{ t("home.noRunningInstances") }}</div>
        <div v-else class="flex flex-col gap-3">
          <div v-for="inst in instances.running" :key="inst.id" class="card-hover p-4 flex items-center gap-4" @click="$router.push(`/instances/${inst.id}`)">
            <StatusDot :status="inst.status" />
            <div class="flex-1 min-w-0">
              <div class="font-medium text-warm-800 dark:text-warm-200">
                {{ inst.config_name }}
              </div>
              <div class="text-secondary truncate">
                {{ inst.pwd }}
              </div>
            </div>
            <GraphCounts :instance="inst" />
            <button class="btn-icon text-coral hover:text-coral-dark flex-shrink-0" :title="t('home.stopInstance')" @click.stop="handleStop(inst)">
              <span class="i-carbon-stop-filled" />
            </button>
          </div>
        </div>
      </div>

      <!-- Quick start -->
      <div>
        <h2 class="section-title">{{ t("home.quickStart") }}</h2>
        <div class="flex flex-wrap gap-3">
          <button class="btn-primary" @click="$router.push('/new')"><span class="i-carbon-add mr-1" /> {{ t("home.startNewInstance") }}</button>
        </div>
      </div>
    </div>

    <!-- Stop confirmation dialog -->
    <el-dialog v-model="showStopConfirm" :title="t('home.stopDialogTitle')" width="400px" :close-on-click-modal="true">
      <p class="text-warm-600 dark:text-warm-300">
        {{ t("home.stopDialogBody", { name: stopTarget?.config_name || "", type: stopTarget?.type || "" }) }}
      </p>
      <template #footer>
        <el-button size="small" @click="showStopConfirm = false">{{ t("common.cancel") }}</el-button>
        <el-button size="small" type="danger" :loading="stopping" @click="confirmStop">{{ t("common.stop") }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus"
import StatusDot from "@/components/common/StatusDot.vue"
import GraphCounts from "@/components/common/GraphCounts.vue"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"

const instances = useInstancesStore()
const { t } = useI18n()
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
    label: t("home.stats.running"),
    value: instances.running.length,
    color: "text-aquamarine",
  },
  {
    label: t("home.stats.terrariums"),
    value: instances.terrariums.length,
    color: "text-iolite dark:text-iolite-light",
  },
  {
    label: t("home.stats.creatures"),
    value: instances.list.reduce((acc, i) => acc + i.creatures.length, 0),
    color: "text-taaffeite dark:text-taaffeite-light",
  },
  {
    label: t("home.stats.channels"),
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
      message: t("home.stoppedMessage", { name: stopTarget.value.config_name }),
      type: "success",
    })
    showStopConfirm.value = false
  } catch (err) {
    ElMessage({
      message: t("home.stopFailedMessage", { message: err.message || "Unknown error" }),
      type: "error",
    })
  } finally {
    stopping.value = false
  }
}
</script>
