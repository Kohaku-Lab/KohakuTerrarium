<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <div class="text-xs uppercase tracking-wider" :class="edge.kind === 'wire' ? 'text-sapphire' : edge.kind === 'direct' ? 'text-sage-shadow dark:text-sage-light' : 'text-aquamarine-shadow dark:text-aquamarine-light'">{{ t(`graph.edge.kind.${edge.kind}`) }}</div>
      <div class="flex items-center gap-2 text-sm min-w-0">
        <button class="font-semibold truncate hover:underline" @click="selectEnd(fromId)">{{ fromName }}</button>
        <span class="text-warm-400">{{ edge.kind === "channel" && edge.mode === "both" ? "⇄" : "→" }}</span>
        <button class="font-semibold truncate hover:underline" @click="selectEnd(toId)">{{ toName }}</button>
      </div>
      <div v-if="edge.count > 1" class="text-xs text-warm-500">{{ t("graph.edge.bundled", { n: edge.count }) }}</div>
    </header>

    <section v-if="members.length" class="flex flex-col gap-1">
      <h4 class="kt-graph-section">{{ t("graph.edge.carriedBy") }}</h4>
      <button v-for="m in members" :key="m.id" class="flex items-center gap-2 text-xs text-left hover:underline" @click="view.select('edge', m.id)">
        <span class="i-carbon-flow-stream text-aquamarine-shadow dark:text-aquamarine-light shrink-0" />
        <span class="truncate">{{ m.text }}</span>
        <span class="i-carbon-edit text-warm-400 shrink-0" />
      </button>
    </section>

    <template v-if="edge.kind === 'channel' && editable">
      <div class="flex gap-1.5">
        <button class="kt-graph-toggle" :class="sends ? 'kt-graph-toggle--on' : ''" @click="toggle('send', !sends)">{{ t("graph.edge.mode.send") }}</button>
        <button class="kt-graph-toggle" :class="listens ? 'kt-graph-toggle--on' : ''" @click="toggle('listen', !listens)">{{ t("graph.edge.mode.listen") }}</button>
      </div>
    </template>

    <template v-if="edge.kind === 'wire' && editable">
      <div class="flex items-center gap-2 text-xs">
        <span class="text-warm-500">{{ t("graph.edge.payload") }}</span>
        <div class="flex rounded-md border border-warm-200 dark:border-warm-700 overflow-hidden">
          <button class="px-2 h-6" :class="edge.withContent !== false ? 'bg-sapphire text-white' : 'text-warm-600 dark:text-warm-300'" @click="setPayload(true)">{{ t("graph.edge.content") }}</button>
          <button class="px-2 h-6" :class="edge.withContent === false ? 'bg-sapphire text-white' : 'text-warm-600 dark:text-warm-300'" @click="setPayload(false)">{{ t("graph.edge.ping") }}</button>
        </div>
      </div>
      <label class="flex flex-col gap-1 text-xs">
        <span class="text-warm-500">{{ t("graph.edge.prompt") }}</span>
        <textarea id="graph-wire-prompt" v-model="prompt" rows="3" class="input-field text-xs font-mono" :placeholder="t('graph.edge.promptPlaceholder')" />
      </label>
      <div class="flex justify-between gap-2">
        <button class="kt-graph-action" @click="actions.reverseWire(edge)"><span class="i-carbon-arrows-horizontal" />{{ t("graph.edge.reverse") }}</button>
        <button class="kt-graph-action kt-graph-action--primary" :disabled="!promptDirty" @click="savePrompt">{{ t("graph.action.save") }}</button>
      </div>
    </template>

    <div v-if="(edge.kind === 'via' && !members.length) || edge.kind === 'lineage'" class="text-xs text-warm-500">{{ t(`graph.edge.explain.${edge.kind}`, { channels: (edge.labels || []).join(", ") }) }}</div>
    <div v-if="edge.kind === 'direct'" class="text-xs text-warm-500">{{ t(edge.implicit ? "graph.edge.explain.direct" : "graph.edge.explain.directAssigned") }}</div>

    <section v-if="editable" class="pt-2 border-t border-warm-200 dark:border-warm-700">
      <button class="kt-graph-action kt-graph-action--danger" @click="actions.removeEdge(edge)"><span class="i-carbon-unlink" />{{ t("graph.action.disconnect") }}</button>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  edge: { type: Object, required: true },
})

const { t } = useI18n()

const listenDir = computed(() => props.edge.kind === "channel" && props.edge.mode === "listen")
const fromId = computed(() => (listenDir.value ? props.edge.target : props.edge.source))
const toId = computed(() => (listenDir.value ? props.edge.source : props.edge.target))
const fromName = computed(() => labelOf(fromId.value))
const toName = computed(() => labelOf(toId.value))
const sends = computed(() => props.edge.mode === "send" || props.edge.mode === "both")
const listens = computed(() => props.edge.mode === "listen" || props.edge.mode === "both")
// Only real model edges (one wire, one membership) can be changed; view-built arrows list what they carry.
const editable = computed(() => (props.edge.count || 1) === 1 && !!props.view.index.get(props.edge.id) && ["wire", "channel"].includes(props.edge.kind))

const members = computed(() =>
  (props.edge.members || [])
    .map((id) => props.view.index.get(id)?.item)
    .filter(Boolean)
    .map((m) => {
      if (m.kind === "wire")
        return {
          id: m.id,
          text: t("graph.edge.wiresTo", {
            name: labelOf(m.source),
            target: labelOf(m.target),
            payload: m.withContent === false ? t("graph.edge.ping") : t("graph.edge.content"),
          }),
        }
      const params = { name: labelOf(m.source), channel: m.channelName }
      const key = m.mode === "both" ? "graph.edge.sendsAndListens" : m.mode === "send" ? "graph.edge.sendsOn" : "graph.edge.listensTo"
      return { id: m.id, text: t(key, params) }
    }),
)

const prompt = ref(props.edge.prompt || "")
watch(
  () => props.edge.id,
  () => {
    prompt.value = props.edge.prompt || ""
  },
)
const promptDirty = computed(() => prompt.value.trim() !== (props.edge.prompt || ""))

function labelOf(id) {
  const entry = props.view.index.get(id)
  if (entry?.kind === "creature" || entry?.kind === "channel") return entry.item.name
  const group = props.view.projection.groups.find((g) => g.id === id)
  return group?.label || id
}

function selectEnd(id) {
  const entry = props.view.index.get(id)
  if (entry) props.view.select(entry.kind, id)
  else props.view.select("group", id)
}

function toggle(direction, enabled) {
  const creature = props.view.model.creatures.find((c) => c.id === props.edge.source)
  if (creature) props.actions.setChannelMembership(creature, props.edge.channelName, direction, enabled)
}

function setPayload(withContent) {
  if (withContent === (props.edge.withContent !== false)) return
  props.actions.updateWire(props.edge, { withContent, prompt: props.edge.prompt || "" })
}

function savePrompt() {
  props.actions.updateWire(props.edge, { withContent: props.edge.withContent !== false, prompt: prompt.value.trim() })
}
</script>
