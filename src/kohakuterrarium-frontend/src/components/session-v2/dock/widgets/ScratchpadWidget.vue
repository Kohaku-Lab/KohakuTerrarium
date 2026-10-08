<template>
  <div class="h-full min-h-0 flex flex-col" data-test="v2-widget-scratchpad">
    <div v-if="!target" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.focus.none", { what: t("widget.scratchpad.what") }) }}</div>
    <template v-else>
      <div class="shrink-0 flex items-center gap-2 px-3 py-1.5 text-[11px] text-warm-500">
        <span class="i-carbon-bot" /><span class="font-medium truncate">{{ target }}</span>
        <span class="flex-1" />
        <span v-if="loading" class="i-carbon-circle-dash animate-spin" />
        <button class="i-carbon-renew hover:text-iolite" :title="t('widget.retry')" @click="load" />
      </div>
      <div v-if="error" class="px-3 py-1 text-[11px] text-coral">{{ error }}</div>
      <div class="flex-1 min-h-0 overflow-y-auto px-3 pb-2 flex flex-col gap-1.5">
        <div v-if="!rows.length && !loading" class="py-4 text-center text-xs text-warm-400">{{ t("widget.scratchpad.empty", { name: target }) }}</div>
        <div v-for="[entryKey, value] in rows" :key="entryKey" class="kt-v2-line group rounded-md border px-2 py-1.5">
          <div class="flex items-center gap-1.5">
            <span class="font-mono text-[11px] text-iolite dark:text-iolite-light truncate flex-1">{{ entryKey }}</span>
            <button class="i-carbon-edit text-xs text-warm-500 hover:text-iolite opacity-0 group-hover:opacity-100 focus-visible:opacity-100" :title="t('widget.scratchpad.edit')" @click="startEdit(entryKey, value)" />
            <button class="i-carbon-trash-can text-xs text-warm-500 hover:text-coral opacity-0 group-hover:opacity-100 focus-visible:opacity-100" :title="t('widget.scratchpad.delete')" @click="remove(entryKey)" />
          </div>
          <template v-if="editing === entryKey">
            <textarea v-model="draft" rows="3" class="mt-1 w-full rounded border kt-v2-line bg-warm-50 dark:bg-warm-950 px-1.5 py-1 font-mono text-[11px] text-warm-800 dark:text-warm-200 focus:outline-none focus:border-iolite" @keydown.enter.exact.prevent="save(entryKey)" @keydown.esc.stop.prevent="editing = null" />
            <div class="mt-1 flex justify-end gap-1">
              <button class="h-6 px-2 rounded text-[11px] text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" @click="editing = null">{{ t("widget.scratchpad.cancel") }}</button>
              <button class="h-6 px-2 rounded text-[11px] text-iolite dark:text-iolite-light hover:bg-iolite/10" @click="save(entryKey)">{{ t("widget.scratchpad.save") }}</button>
            </div>
          </template>
          <div v-else class="mt-0.5 font-mono text-[11px] text-warm-600 dark:text-warm-300 whitespace-pre-wrap break-words" :class="mode === 'side' ? '' : 'line-clamp-4'">{{ value }}</div>
        </div>
      </div>
      <form class="kt-v2-line shrink-0 flex items-center gap-1.5 px-3 py-2 border-t" @submit.prevent="add">
        <input v-model="newKey" :placeholder="t('widget.scratchpad.key')" class="w-24 min-w-0 rounded border kt-v2-edge bg-transparent px-1.5 py-0.5 font-mono text-[11px] text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite" />
        <input v-model="newValue" :placeholder="t('widget.scratchpad.value')" class="flex-1 min-w-0 rounded border kt-v2-edge bg-transparent px-1.5 py-0.5 font-mono text-[11px] text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite" />
        <button type="submit" class="h-6 px-2 rounded text-[11px] text-iolite dark:text-iolite-light hover:bg-iolite/10 disabled:opacity-40" :disabled="!newKey.trim()">{{ t("widget.scratchpad.add") }}</button>
      </form>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { focusedCreature, scratchpadRows } from "@/components/session-v2/model/widgets/widgetData"
import { useScratchpadStore } from "@/stores/scratchpad"

/** The focused creature's scratchpad: read every key, edit, delete, add. Fetched on open and on focus change. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const scratchpad = useScratchpadStore()

const routingId = computed(() => ctx.instance.value?.id || ctx.sessionId.value)
const target = computed(() => focusedCreature(ctx.instance.value, ctx.chat.activeTab, ctx.chat._rootSourceName))
const key = computed(() => `${routingId.value}:${target.value}`)
const rows = computed(() => scratchpadRows(scratchpad.getFor(routingId.value, target.value)))
const loading = computed(() => !!scratchpad.loading[key.value])
const error = computed(() => scratchpad.error[key.value] || actionError.value)
const actionError = ref("")
const editing = ref(null)
const draft = ref("")
const newKey = ref("")
const newValue = ref("")

function load() {
  if (target.value) scratchpad.fetch(routingId.value, target.value)
}

/** Apply `updates`; true on success. A failure keeps the user's input and shows the error. */
async function patch(updates) {
  actionError.value = ""
  try {
    await scratchpad.patch(routingId.value, updates, target.value)
    return true
  } catch (err) {
    actionError.value = err?.response?.data?.detail || err?.message || String(err)
    return false
  }
}

function startEdit(k, value) {
  editing.value = k
  draft.value = value
}

async function save(k) {
  if (await patch({ [k]: draft.value })) editing.value = null
}

function remove(k) {
  return patch({ [k]: null })
}

async function add() {
  const k = newKey.value.trim()
  if (!k) return
  if (!(await patch({ [k]: newValue.value }))) return
  newKey.value = ""
  newValue.value = ""
}

watch(key, load, { immediate: true })
</script>
