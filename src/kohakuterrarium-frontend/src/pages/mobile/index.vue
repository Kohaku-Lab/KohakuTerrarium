<template>
  <MobileShell title="KohakuTerrarium">
    <div class="h-full overflow-y-auto p-4">
      <!-- Running instances -->
      <div class="mb-6">
        <div class="text-[10px] text-warm-400 uppercase tracking-wider font-medium mb-2">Running Instances</div>
        <div v-if="instances.list.length === 0" class="text-sm text-warm-400 py-8 text-center rounded-lg border border-dashed border-warm-300 dark:border-warm-700">No running instances</div>
        <div v-else class="flex flex-col gap-2">
          <div v-for="inst in instances.list" :key="inst.id" class="flex items-center gap-3 px-4 py-3 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 cursor-pointer transition-colors hover:border-iolite/30 active:bg-warm-50 dark:active:bg-warm-800" @click="$router.push(`/mobile/${inst.id}`)">
            <div :class="inst.type === 'terrarium' ? 'i-carbon-network-4 text-taaffeite' : 'i-carbon-bot text-iolite'" class="text-lg shrink-0" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-warm-700 dark:text-warm-200 truncate">{{ inst.config_name }}</div>
              <div class="text-[11px] text-warm-400">{{ inst.type }} · {{ inst.model || "default" }}</div>
            </div>
            <span class="w-2.5 h-2.5 rounded-full shrink-0" :class="inst.status === 'running' ? 'bg-aquamarine' : 'bg-warm-400'" />
            <button class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-coral shrink-0" title="Stop" @click.stop="handleStop(inst)">
              <div class="i-carbon-stop text-sm" />
            </button>
          </div>
        </div>
      </div>

      <!-- Quick actions -->
      <div>
        <div class="text-[10px] text-warm-400 uppercase tracking-wider font-medium mb-2">Quick Actions</div>
        <div class="grid grid-cols-2 gap-2">
          <router-link to="/mobile/new" class="flex flex-col items-center gap-2 px-4 py-5 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 hover:border-iolite/30 active:bg-warm-50 transition-colors">
            <div class="i-carbon-add-large text-xl text-iolite" />
            <span class="text-xs text-warm-600 dark:text-warm-300">Start New</span>
          </router-link>
          <router-link to="/mobile/sessions" class="flex flex-col items-center gap-2 px-4 py-5 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 hover:border-iolite/30 active:bg-warm-50 transition-colors">
            <div class="i-carbon-recently-viewed text-xl text-amber" />
            <span class="text-xs text-warm-600 dark:text-warm-300">Sessions</span>
          </router-link>
          <router-link to="/mobile/settings" class="flex flex-col items-center gap-2 px-4 py-5 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 hover:border-iolite/30 active:bg-warm-50 transition-colors">
            <div class="i-carbon-settings text-xl text-warm-500" />
            <span class="text-xs text-warm-600 dark:text-warm-300">Settings</span>
          </router-link>
          <router-link to="/mobile/registry" class="flex flex-col items-center gap-2 px-4 py-5 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 hover:border-iolite/30 active:bg-warm-50 transition-colors">
            <div class="i-carbon-catalog text-xl text-aquamarine" />
            <span class="text-xs text-warm-600 dark:text-warm-300">Registry</span>
          </router-link>
        </div>
      </div>
    </div>

    <!-- Stop dialog -->
    <el-dialog v-model="showStopConfirm" title="Stop Instance" width="90%" :close-on-click-modal="true">
      <p class="text-warm-600 dark:text-warm-300 text-sm">
        Stop <strong>{{ stopTarget?.config_name }}</strong
        >?
      </p>
      <template #footer>
        <el-button size="small" @click="showStopConfirm = false">Cancel</el-button>
        <el-button size="small" type="danger" :loading="stopping" @click="confirmStop">Stop</el-button>
      </template>
    </el-dialog>
  </MobileShell>
</template>

<script setup>
import { ref } from "vue"

import MobileShell from "@/components/mobile/MobileShell.vue"
import { useInstancesStore } from "@/stores/instances"

const instances = useInstancesStore()
instances.fetchAll()

const showStopConfirm = ref(false)
const stopTarget = ref(null)
const stopping = ref(false)

function handleStop(inst) {
  stopTarget.value = inst
  showStopConfirm.value = true
}

async function confirmStop() {
  if (!stopTarget.value) return
  stopping.value = true
  try {
    await instances.stop(stopTarget.value.id)
    showStopConfirm.value = false
  } catch (err) {
    console.error("Stop failed:", err)
  } finally {
    stopping.value = false
  }
}
</script>
