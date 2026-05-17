<template>
  <el-dialog v-model="visible" :title="t('cluster.wizard.title')" width="560px">
    <div class="space-y-3 text-[13px]">
      <p class="text-warm-500 dark:text-warm-400">{{ t("cluster.wizard.description") }}</p>

      <div>
        <label class="block text-[11px] text-warm-400 mb-1">{{ t("cluster.wizard.hostUrl") }}</label>
        <el-input v-model="hostUrl" size="small" placeholder="wss://10.0.0.1:8100" />
      </div>

      <div>
        <label class="block text-[11px] text-warm-400 mb-1">{{ t("cluster.wizard.token") }}</label>
        <el-input v-model="token" size="small" type="password" show-password :placeholder="t('cluster.wizard.tokenPlaceholder')" />
        <p class="text-[11px] text-warm-400 mt-1">{{ t("cluster.wizard.tokenHint") }}</p>
      </div>

      <div>
        <label class="block text-[11px] text-warm-400 mb-1">{{ t("cluster.wizard.workerName") }}</label>
        <el-input v-model="workerName" size="small" placeholder="worker-1" />
      </div>

      <div class="rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 p-3">
        <div class="flex items-center justify-between mb-2">
          <span class="text-[11px] text-warm-400 uppercase tracking-wider">{{ t("cluster.wizard.command") }}</span>
          <el-button size="small" plain @click="copyCommand">
            <span class="i-carbon-copy mr-1" />
            {{ t("cluster.wizard.copy") }}
          </el-button>
        </div>
        <pre class="font-mono text-[12px] whitespace-pre-wrap break-all text-warm-700 dark:text-warm-300">{{ commandText }}</pre>
      </div>

      <p class="text-[11px] text-warm-400">{{ t("cluster.wizard.runHint") }}</p>
    </div>

    <template #footer>
      <el-button @click="visible = false">{{ t("common.close") }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(["update:modelValue"])
const { t } = useI18n()
const cluster = useClusterStore()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const hostUrl = ref("")
const token = ref("")
const workerName = ref("worker-1")

watch(visible, (v) => {
  if (!v) return
  // Pre-fill from cluster store / current host info.
  if (!hostUrl.value) {
    try {
      const u = new URL(window.location.href)
      const proto = u.protocol === "https:" ? "wss" : "ws"
      hostUrl.value = `${proto}://${u.hostname}:8100`
    } catch {
      hostUrl.value = ""
    }
  }
  if (!token.value && cluster.latestToken) {
    token.value = cluster.latestToken
  }
})

const commandText = computed(() => {
  const t1 = token.value || "<paste-token-here>"
  const u = hostUrl.value || "wss://<host>:8100"
  const n = workerName.value || "worker-1"
  return `kt client --host ${u} --token ${t1} --name ${n}`
})

async function copyCommand() {
  try {
    await navigator.clipboard.writeText(commandText.value)
    ElMessage.success(t("cluster.wizard.copied"))
  } catch {
    ElMessage.warning(t("cluster.wizard.copyFailed"))
  }
}
</script>
