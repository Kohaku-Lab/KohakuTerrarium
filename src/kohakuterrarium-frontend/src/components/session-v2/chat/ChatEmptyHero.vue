<template>
  <div class="flex flex-col items-center gap-2 text-center select-none">
    <BrandMark class="w-10 h-10 rounded-full opacity-90" />
    <h2 class="text-xl font-semibold text-warm-800 dark:text-warm-100">{{ title }}</h2>
    <p class="text-xs text-warm-500">{{ t("hero.hint") }}</p>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { creatureNameFor } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import BrandMark from "@/components/shell/BrandMark.vue"

/** The greeting above the centered composer of an empty conversation, naming the creature or channel. */
const props = defineProps({
  tabKey: { type: String, default: null },
})

const ctx = useSessionV2()
const t = useV2T()
const title = computed(() => {
  const key = props.tabKey || ""
  if (key.startsWith("ch:")) return t("hero.channel", { name: key.slice(3) })
  return t("hero.title", { name: creatureNameFor(key, ctx.chat._rootSourceName) || t("hero.anyone") })
})
</script>
