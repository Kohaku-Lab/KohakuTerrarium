<template>
  <div class="flex flex-col gap-4">
    <CodexUsageCard :usage="codex.usage.value" :loading="codex.loading.value" :initial="codex.initial.value" :error="codex.error.value" :stale="codex.stale.value" :stale-at="codex.staleAt.value" :redeeming-id="redeemingId" @refresh="codex.refresh(props.node)" @redeem="redeemResetCredit" />
    <AntigravityUsageCard :usage="antigravity.usage.value" :loading="antigravity.loading.value" :initial="antigravity.initial.value" :error="antigravity.error.value" :stale="antigravity.stale.value" :stale-at="antigravity.staleAt.value" @refresh="antigravity.refresh(props.node)" />
    <GrokUsageCard :usage="grok.usage.value" :loading="grok.loading.value" :initial="grok.initial.value" :error="grok.error.value" :stale="grok.stale.value" :stale-at="grok.staleAt.value" @refresh="grok.refresh(props.node)" />
  </div>
</template>

<script setup>
import { onUnmounted, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

import CodexUsageCard from "./CodexUsageCard.vue"
import AntigravityUsageCard from "./AntigravityUsageCard.vue"
import GrokUsageCard from "./GrokUsageCard.vue"
import { useProviderUsage } from "./providerUsage"

const props = defineProps({
  node: { type: String, default: "_host" },
  active: { type: Boolean, default: false },
})

const { t } = useI18n()
const codex = useProviderUsage((node) => settingsAPI.getCodexUsage(node), {
  t,
  fallbackKey: "settings.account.codex.loadFailed",
})
const grok = useProviderUsage((node) => settingsAPI.getGrokUsage(node), {
  t,
  fallbackKey: "settings.account.grok.loadFailed",
})

const antigravity = useProviderUsage((node) => (!node || node === "_host" ? settingsAPI.getAntigravityUsage(node) : Promise.resolve({ status: "unsupported" })), { t, fallbackKey: "settings.account.antigravity.loadFailed" })

const redeemingId = ref("")
let redeemGeneration = 0

function invalidate(node) {
  redeemGeneration += 1
  redeemingId.value = ""
  codex.invalidate(node)
  grok.invalidate(node)
  antigravity.invalidate(node)
}

function load(node) {
  codex.refresh(node)
  grok.refresh(node)
  antigravity.refresh(node)
}

watch(
  () => [props.node, props.active],
  ([node, active], previous) => {
    if (!previous || node !== previous[0]) invalidate(node)
    if (active) load(node)
  },
  { immediate: true },
)

onUnmounted(() => {
  invalidate(props.node)
})

async function redeemResetCredit(credit) {
  if (!credit?.id || redeemingId.value) return
  const requestNode = props.node
  const requestGeneration = redeemGeneration
  redeemingId.value = credit.id
  try {
    const res = await settingsAPI.codexResetConsume({ idempotencyKey: `reset-${credit.id}`, creditId: credit.id }, requestNode)
    if (requestGeneration !== redeemGeneration || props.node !== requestNode) return
    switch (res?.outcome) {
      case "reset":
        ElMessage.success(t("settings.account.resetRedeemed"))
        break
      case "nothingToReset":
        ElMessage.info(t("settings.account.resetNothing"))
        break
      case "noCredit":
        ElMessage.warning(t("settings.account.resetNoCredit"))
        break
      case "alreadyRedeemed":
        ElMessage.info(t("settings.account.resetAlready"))
        break
      default:
        ElMessage.info(String(res?.outcome || ""))
    }
    await codex.refresh(requestNode)
  } catch {
    if (requestGeneration !== redeemGeneration || props.node !== requestNode) return
    ElMessage.error(t("settings.account.resetFailed"))
  } finally {
    if (requestGeneration === redeemGeneration) redeemingId.value = ""
  }
}
</script>
