<template>
  <button type="button" class="min-w-0 h-9 px-2 rounded-lg flex items-center gap-1 text-xs text-warm-500 active:bg-warm-200/60 dark:active:bg-warm-800" :aria-label="agent ? t('phone.modelFor', { name: agent }) : t('phone.modelTitle')" data-test="phone-model-chip" @click="ctx.openSheet('model', { agent })">
    <span class="i-carbon-chip shrink-0" />
    <span v-if="agent" class="shrink-0 max-w-[6rem] truncate text-warm-700 dark:text-warm-200">{{ agent }}</span>
    <span v-if="agent" class="shrink-0 text-warm-400">·</span>
    <span class="min-w-0 truncate font-mono">{{ agent ? bareModelName(model) || t("phone.noModel") : t("phone.pickModel") }}</span>
    <span class="i-carbon-chevron-down shrink-0 text-warm-400" />
  </button>
</template>

<script setup>
import { computed } from "vue"

import { creatureOfTab, modelOfCreature } from "@/components/session-v2/model/phone/phoneModel"
import { bareModelName } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The phone composer's model chip: "agent · model" of the open conversation; it opens the model sheet on that agent (on none in a channel). */
const ctx = useSessionV2()
const t = useV2T()
const agent = computed(() => creatureOfTab(ctx.instance.value, ctx.chat.activeTab, ctx.chat._rootSourceName))
const model = computed(() => modelOfCreature(ctx.instance.value, ctx.chat, agent.value))
</script>
