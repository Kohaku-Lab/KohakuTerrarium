<template>
  <section data-provider="codex" class="card border-solid p-4 sm:p-5 flex flex-col gap-4 min-w-0">
    <header class="flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <h3 class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.account.codex.title") }}</h3>
        <span v-if="headerPlan" class="text-[11px] rounded px-2 py-0.5 bg-warm-100 dark:bg-warm-700 text-warm-500 capitalize">{{ headerPlan }}</span>
      </div>
      <el-button size="small" data-refresh :loading="loading && !initial" :disabled="loading" @click="emit('refresh')">
        {{ t("common.refresh") }}
      </el-button>
    </header>

    <div v-if="initial" data-skeleton class="flex flex-col gap-2" aria-hidden="true">
      <div class="h-3 w-2/3 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
    </div>

    <template v-else>
      <p v-if="error" class="text-sm text-coral">{{ error }}</p>
      <p v-if="stale" class="text-xs text-amber-shadow dark:text-amber-light">
        {{ t("settings.account.codex.stale", { value: staleAt }) }}
      </p>

      <p v-if="usage?.status === 'not_logged_in'" class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.notLoggedIn") }}</p>
      <p v-else-if="usage?.status === 'no_data_yet'" class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.noDataYet") }}</p>
      <template v-else-if="usage?.status === 'ok'">
        <div v-for="(snap, index) in snapshots" :key="snap.limit_id" data-snapshot class="flex flex-col gap-3" :class="{ 'border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-4': index > 0 }">
          <div v-if="showSnapshotHeading(snap)" class="flex items-center justify-between gap-2">
            <h4 class="text-sm font-medium text-warm-600 dark:text-warm-400">{{ snap.limit_name || snap.limit_id || t("settings.account.defaultLimit") }}</h4>
            <span v-if="snap.plan_type && !headerPlan" class="text-[11px] text-warm-400 capitalize">{{ snap.plan_type }}</span>
          </div>
          <UsageWindow :label="t('settings.account.shortTermWindow')" :window="snap.primary" />
          <UsageWindow :label="t('settings.account.weeklyWindow')" :window="snap.secondary" />
          <div v-if="snap.credits" class="text-xs text-warm-500 flex items-center gap-2">
            <span>{{ t("settings.account.credits") }}</span>
            <span v-if="snap.credits.unlimited" class="text-iolite">{{ t("settings.account.unlimited") }}</span>
            <span v-else-if="snap.credits.has_credits && snap.credits.balance">{{ t("settings.account.balance", { value: snap.credits.balance }) }}</span>
            <span v-else class="text-warm-400">{{ t("settings.account.noCredits") }}</span>
          </div>
          <p v-if="snap.rate_limit_reached_type" class="text-xs text-coral">{{ t("settings.account.overageLimitReached") }}</p>
        </div>
        <p v-if="usage.promo_message" class="border-0 border-l-2 border-solid border-iolite pl-3 text-xs text-warm-500">{{ usage.promo_message }}</p>

        <section v-if="credits.length" data-reset-credits :aria-label="t('settings.account.codex.resetCredits')" class="border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-4 flex flex-col gap-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="text-sm font-medium text-warm-600 dark:text-warm-400">{{ t("settings.account.codex.resetCredits") }}</h4>
            <span class="text-xs text-warm-500">{{ t("settings.account.codex.resetCount", { count: credits.length }) }}</span>
          </div>
          <div v-for="credit in credits" :key="credit.id" class="flex items-start justify-between gap-3 text-xs">
            <div class="min-w-0 flex-1 flex flex-col gap-1">
              <div class="text-warm-700 dark:text-warm-300 break-words">{{ credit.title || credit.reset_type || t("settings.account.resetCredit") }}</div>
              <time v-if="formatCreditExpiry(credit.expires_at)" :datetime="credit.expires_at" :title="formatCreditExpiry(credit.expires_at, 'full')" :aria-label="t('settings.account.resetExpires', { value: formatCreditExpiry(credit.expires_at, 'full') })" class="text-warm-500">
                {{ t("settings.account.resetExpires", { value: formatCreditExpiry(credit.expires_at) }) }}
              </time>
              <details v-if="credit.description" class="text-warm-500">
                <summary class="cursor-pointer w-fit hover:text-iolite focus-visible:outline-iolite">{{ t("settings.account.codex.creditDetails") }}</summary>
                <p class="mt-1 break-words leading-relaxed">{{ credit.description }}</p>
              </details>
            </div>
            <el-button size="small" type="primary" plain data-reset-redeem :loading="redeemingId === credit.id" :disabled="!!redeemingId" @click="emit('redeem', credit)">
              {{ t("settings.account.resetRedeem") }}
            </el-button>
          </div>
        </section>

        <footer class="border-0 border-t border-solid border-warm-200 dark:border-warm-700 pt-3 text-[11px] text-warm-400">
          <time v-if="formatDateTime(usage.captured_at)" :title="formatDateTime(usage.captured_at)" :aria-label="t('settings.account.updatedAt', { value: formatDateTime(usage.captured_at) })">
            {{ t("settings.account.updatedAt", { value: formatDateTime(usage.captured_at, "updated") }) }}
          </time>
          <span v-else>{{ t("settings.account.updatedUnknown") }}</span>
        </footer>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import { formatCreditExpiry, formatDateTime } from "./usageFormat"
import UsageWindow from "./UsageWindow.vue"

const props = defineProps({
  usage: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  initial: { type: Boolean, default: false },
  error: { type: String, default: "" },
  stale: { type: Boolean, default: false },
  staleAt: { type: String, default: "" },
  redeemingId: { type: String, default: "" },
})

const emit = defineEmits(["refresh", "redeem"])
const { t } = useI18n()
const snapshots = computed(() => (props.usage?.status === "ok" ? props.usage.snapshots || [] : []))
const headerPlan = computed(() => (snapshots.value.length === 1 ? snapshots.value[0].plan_type : null))
const credits = computed(() => props.usage?.reset_credits?.credits || [])

function showSnapshotHeading(snap) {
  if (snapshots.value.length > 1) return true
  const name = String(snap.limit_name || snap.limit_id || "")
    .trim()
    .toLowerCase()
  return !["", "codex", "default"].includes(name)
}
</script>
