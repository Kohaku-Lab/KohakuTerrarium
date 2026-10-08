<template>
  <div class="h-full min-h-0 flex flex-col" data-test="v2-widget-plugins">
    <div v-if="!target" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.focus.none", { what: t("widget.plugins.what") }) }}</div>
    <template v-else>
      <div class="shrink-0 flex items-center gap-2 px-3 py-1.5 text-[11px] text-warm-500">
        <span class="i-carbon-bot" /><span class="font-medium truncate">{{ target }}</span>
        <span class="flex-1" />
        <span v-if="loading" class="i-carbon-circle-dash animate-spin" />
        <button class="i-carbon-renew hover:text-iolite" :title="t('widget.retry')" @click="load" />
      </div>
      <div v-if="error" class="px-3 py-1 text-[11px] text-coral">{{ error }}</div>
      <div class="flex-1 min-h-0 overflow-y-auto pb-2">
        <div v-if="!groups.length && !loading" class="px-3 py-4 text-center text-xs text-warm-400">{{ t("widget.plugins.empty") }}</div>
        <section v-for="g in groups" :key="g.type" class="mt-1">
          <div class="px-3 py-1 text-[10px] uppercase tracking-wider text-warm-400">{{ typeLabel(g.type) }} · {{ g.items.length }}</div>
          <div v-for="m in g.items" :key="`${m.type}:${m.name}`" class="flex items-center gap-2 px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800/60">
            <div class="min-w-0 flex-1">
              <div class="text-[12px] text-warm-800 dark:text-warm-100 truncate font-mono">{{ m.name }}</div>
              <div v-if="m.description" class="text-[11px] text-warm-500" :class="mode === 'side' ? '' : 'truncate'">{{ m.description }}</div>
            </div>
            <span v-if="m.priority != null" class="text-[10px] text-warm-500 font-mono shrink-0">p{{ m.priority }}</span>
            <el-switch v-if="m.enabled === true || m.enabled === false" :model-value="m.enabled" size="small" :loading="toggling === `${m.type}:${m.name}`" @change="toggle(m)" />
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { focusedCreature, moduleGroups } from "@/components/session-v2/model/widgets/widgetData"
import { moduleAPI } from "@/utils/api"

/** The focused creature's plugins, tools and triggers, with enable toggles where the module has one. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const modules = ref([])
const loading = ref(false)
const error = ref("")
const toggling = ref("")
let loadGen = 0

const target = computed(() => focusedCreature(ctx.instance.value, ctx.chat.activeTab, ctx.chat._rootSourceName))
const groups = computed(() => moduleGroups(modules.value))

function typeLabel(type) {
  const key = `widget.plugins.type.${type}`
  const label = t(key)
  return label === key ? type : label
}

async function load() {
  const sid = ctx.sessionId.value
  const creature = target.value
  const gen = ++loadGen
  if (!sid || !creature) {
    modules.value = []
    return
  }
  loading.value = true
  error.value = ""
  try {
    const fresh = await moduleAPI.list(sid, creature)
    if (gen === loadGen) modules.value = Array.isArray(fresh) ? fresh : []
  } catch (err) {
    if (gen === loadGen) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (gen === loadGen) loading.value = false
  }
}

async function toggle(m) {
  const key = `${m.type}:${m.name}`
  toggling.value = key
  error.value = ""
  m.enabled = !m.enabled
  try {
    await moduleAPI.toggle(ctx.sessionId.value, target.value, m.type, m.name)
    await load()
  } catch (err) {
    m.enabled = !m.enabled
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    toggling.value = ""
  }
}

watch(() => `${ctx.sessionId.value}::${target.value}`, load, { immediate: true })
</script>
