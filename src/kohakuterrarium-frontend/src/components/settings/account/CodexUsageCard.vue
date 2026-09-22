<template>
  <section data-provider="codex" class="flex flex-col gap-3">
    <div class="flex items-center justify-between gap-3">
      <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.account.codex.title") }}</div>
      <el-button v-if="!initial" size="small" data-refresh :loading="loading" :disabled="loading" @click="emit('refresh')">
        {{ t("common.refresh") }}
      </el-button>
    </div>

    <div v-if="initial" data-skeleton class="card p-4 flex flex-col gap-2" aria-hidden="true">
      <div class="h-3 w-2/3 rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
      <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 animate-pulse" />
    </div>

    <template v-else>
      <p v-if="error" class="card p-4 border-l-3 border-l-coral text-sm text-warm-600 dark:text-warm-400">{{ error }}</p>
      <p v-if="stale" class="text-[11px] text-amber-shadow dark:text-amber-light">
        {{ t("settings.account.codex.stale", { value: staleAt }) }}
      </p>

      <div v-if="usage?.status === 'not_logged_in'" class="card p-4 border-l-3 border-l-warm-400">
        <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.notLoggedIn") }}</p>
      </div>
      <div v-else-if="usage?.status === 'no_data_yet'" class="card p-4 border-l-3 border-l-warm-400">
        <p class="text-sm text-warm-600 dark:text-warm-400">{{ t("settings.account.noDataYet") }}</p>
      </div>
      <template v-else-if="usage?.status === 'ok'">
        <div v-if="usage.captured_at" class="text-[11px] text-warm-400">
          {{ t("settings.account.capturedAt", { value: formatDateTime(usage.captured_at) }) }}
        </div>
        <div v-for="snap in usage.snapshots || []" :key="snap.limit_id" class="card p-4 flex flex-col gap-3">
          <div class="flex items-center justify-between">
            <div class="font-medium text-warm-700 dark:text-warm-300">
              {{ snap.limit_name || snap.limit_id || t("settings.account.defaultLimit") }}
            </div>
            <div v-if="snap.plan_type" class="text-[11px] text-warm-400 capitalize">{{ snap.plan_type }}</div>
          </div>
          <UsageWindow :label="t('settings.account.shortTermWindow')" :window="snap.primary" />
          <UsageWindow :label="t('settings.account.weeklyWindow')" :window="snap.secondary" />
          <div v-if="snap.credits" class="text-xs text-warm-500 flex items-center gap-2">
            <span class="font-medium text-warm-600 dark:text-warm-400">{{ t("settings.account.credits") }}</span>
            <span v-if="snap.credits.unlimited" class="text-iolite">{{ t("settings.account.unlimited") }}</span>
            <span v-else-if="snap.credits.has_credits && snap.credits.balance">
              {{ t("settings.account.balance", { value: snap.credits.balance }) }}
            </span>
            <span v-else class="text-warm-400">{{ t("settings.account.noCredits") }}</span>
          </div>
          <div v-if="snap.rate_limit_reached_type" class="text-xs text-coral">
            {{ t("settings.account.overageLimitReached") }}
          </div>
        </div>
        <div v-if="usage.promo_message" class="card p-3 border-l-3 border-l-iolite text-xs text-warm-600 dark:text-warm-400">
          {{ usage.promo_message }}
        </div>
      </template>

      <div v-if="credits.length" class="card p-4 flex flex-col gap-3">
        <div class="font-medium text-warm-700 dark:text-warm-300">{{ t("settings.account.resetCredits") }}</div>
        <div v-for="credit in credits" :key="credit.id" class="flex items-center justify-between gap-3 text-xs">
          <div class="min-w-0">
            <div class="text-warm-700 dark:text-warm-300 truncate">{{ credit.title || credit.reset_type || t("settings.account.resetCredit") }}</div>
            <div v-if="credit.description" class="text-[11px] text-warm-400 truncate">{{ credit.description }}</div>
            <div v-if="credit.expires_at" class="text-[11px] text-warm-400">{{ t("settings.account.resetExpires", { value: credit.expires_at }) }}</div>
          </div>
          <el-button size="small" type="primary" plain data-reset-redeem :loading="redeemingId === credit.id" :disabled="!!redeemingId" @click="emit('redeem', credit)">
            {{ t("settings.account.resetRedeem") }}
          </el-button>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import { formatDateTime } from "./usageFormat"
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
const credits = computed(() => props.usage?.reset_credits?.credits || [])
</script>
