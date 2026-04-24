<template>
  <div class="flex flex-col gap-1">
    <div v-if="loading" class="text-[11px] text-warm-500 italic">
      {{ t("studio.common.loading") }}
    </div>
    <div v-else-if="!matches.length" class="text-[11px] text-warm-500 italic">
      {{ t("studio.module.usedIn.empty") }}
    </div>
    <template v-else>
      <div class="text-[11px] text-warm-500">
        {{ t("studio.module.usedIn.summary", { n: matches.length }) }}
      </div>
      <div class="flex flex-wrap gap-1">
        <button v-for="c in matches" :key="c.name" class="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-warm-200 dark:border-warm-800 bg-warm-50 dark:bg-warm-950 hover:border-iolite dark:hover:border-iolite-light text-[11px] font-mono text-warm-700 dark:text-warm-300 hover:text-iolite" @click="$emit('open', c.name)">
          <div class="i-carbon-bot text-sm" />
          {{ c.name }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { creatureAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
  /** Bump to re-fetch after a module save. */
  refreshKey: { type: Number, default: 0 },
})

const emit = defineEmits(["open", "count-change"])

// Map plural module-kind → the creature-config list key that holds
// wired entries of that kind. Triggers share the tools list (via
// `type: trigger`).
const LIST_KEY_FOR_KIND = {
  tools: "tools",
  subagents: "subagents",
  triggers: "tools",
  plugins: "plugins",
  inputs: "inputs",
  outputs: "outputs",
}

const loading = ref(false)
const creatures = ref([])

async function refresh() {
  loading.value = true
  try {
    const list = await creatureAPI.list()
    // Load each creature's config to see what it wires. Serial is
    // fine at typical workspace sizes (<20 creatures).
    const fetched = await Promise.all(
      (list || []).map(async (c) => {
        try {
          return await creatureAPI.load(c.name)
        } catch {
          return null
        }
      }),
    )
    creatures.value = fetched.filter(Boolean)
  } catch {
    creatures.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.kind, props.name, props.refreshKey],
  () => refresh(),
  { immediate: true },
)

const matches = computed(() => {
  const listKey = LIST_KEY_FOR_KIND[props.kind]
  if (!listKey) return []
  return creatures.value.filter((c) => {
    const list = c?.config?.[listKey]
    if (!Array.isArray(list)) return false
    for (const entry of list) {
      if (entry?.name !== props.name) continue
      // For triggers, only count entries that declare type:trigger.
      if (props.kind === "triggers" && entry.type !== "trigger") continue
      if (props.kind === "tools" && entry.type === "trigger") continue
      return true
    }
    return false
  })
})

watch(matches, (v) => emit("count-change", v.length), { immediate: true })
</script>
