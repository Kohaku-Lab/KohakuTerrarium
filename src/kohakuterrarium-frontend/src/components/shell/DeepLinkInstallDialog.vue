<template>
  <el-dialog v-model="open" :title="t('deepLink.install.title')" width="440px" align-center :close-on-click-modal="!busy" data-test="deep-link-install">
    <div class="flex flex-col gap-2 text-sm text-warm-700 dark:text-warm-300">
      <div>{{ t("deepLink.install.body") }}</div>
      <code class="px-2 py-1 rounded bg-warm-100 dark:bg-warm-800 font-mono text-xs" data-test="deep-link-spec">{{ spec }}</code>
      <div class="text-[11px] text-warm-500">{{ t("deepLink.install.warning") }}</div>
      <div v-if="state === 'done'" class="text-iolite text-xs" data-test="deep-link-done">{{ t("deepLink.install.done") }}</div>
      <div v-if="state === 'failed'" class="text-coral text-xs" data-test="deep-link-failed">{{ message || t("deepLink.install.failed") }}</div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button data-test="deep-link-cancel" :disabled="busy" @click="open = false">{{ state === "done" ? t("deepLink.install.close") : t("deepLink.install.cancel") }}</el-button>
        <el-button v-if="state !== 'done'" type="primary" data-test="deep-link-confirm" :loading="busy" @click="confirm">{{ t("deepLink.install.confirm") }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { useMarketplaceStore } from "@/stores/marketplace"
import { DEEP_LINK_INSTALL_EVENT } from "@/utils/deepLink"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const marketplace = useMarketplaceStore()
const open = ref(false)
const spec = ref("")
const state = ref("idle")
const message = ref("")
const busy = computed(() => state.value === "installing")

function onInstallLink(event) {
  const next = String(event.detail?.spec || "")
  if (!next || busy.value) return
  spec.value = next
  state.value = "idle"
  message.value = ""
  open.value = true
}

async function confirm() {
  state.value = "installing"
  message.value = ""
  try {
    await marketplace.install(spec.value)
    state.value = "done"
  } catch (error) {
    state.value = "failed"
    message.value = error?.response?.data?.detail || ""
  }
}

onMounted(() => window.addEventListener(DEEP_LINK_INSTALL_EVENT, onInstallLink))
onBeforeUnmount(() => window.removeEventListener(DEEP_LINK_INSTALL_EVENT, onInstallLink))
</script>
