<template>
  <PhoneSheet :title="sessionName" :close-label="t('close')" test-id="phone-menu" @close="ctx.closeSheet()">
    <template v-if="conversationKey">
      <div :class="HEAD">{{ t("phone.thisConversation") }}</div>
      <button v-if="agentName" type="button" :class="ROW" data-test="phone-menu-model" @click="ctx.openSheet('model', { agent: agentName })">
        <span class="i-carbon-chip text-lg text-warm-500 shrink-0" />
        <span class="flex-1 min-w-0">
          <span class="block text-[15px] text-warm-800 dark:text-warm-100">{{ t("phone.model") }}</span>
          <span class="block truncate font-mono text-xs text-warm-500">{{ model || t("phone.noModel") }}</span>
        </span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
      <button type="button" :class="ROW" :disabled="!actions" data-test="phone-menu-compact" @click="run('compact')">
        <span class="i-carbon-collapse-all text-lg text-warm-500 shrink-0" /><span class="flex-1 text-[15px] text-warm-800 dark:text-warm-100">{{ t("phone.compact") }}</span>
      </button>
      <button type="button" :class="ROW" :disabled="!actions" data-test="phone-menu-clear" @click="run('clear')">
        <span class="i-carbon-clean text-lg text-warm-500 shrink-0" /><span class="flex-1 text-[15px] text-warm-800 dark:text-warm-100">{{ t("phone.clear") }}</span>
      </button>
    </template>

    <div :class="HEAD">{{ t("phone.tools") }}</div>
    <button v-for="tool in tools" :key="tool.id" type="button" :class="ROW" :data-test="`phone-tool-${tool.id}`" @click="openTool(tool)">
      <span :class="tool.icon" class="text-lg text-warm-500 shrink-0" />
      <span class="flex-1 text-[15px] text-warm-800 dark:text-warm-100">{{ t(tool.target === "side" ? `side.${tool.id}.title` : `widget.${tool.id}.title`) }}</span>
      <span v-if="tool.count" class="px-2 rounded-full bg-warm-200 dark:bg-warm-800 text-xs font-mono text-warm-600 dark:text-warm-300">{{ tool.count }}</span>
      <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
    </button>

    <div :class="HEAD">{{ t("phone.session") }}</div>
    <button type="button" :class="ROW" data-test="phone-menu-add-creature" @click="ctx.openAdd('creature')">
      <span class="i-carbon-add-alt text-lg text-warm-500 shrink-0" /><span class="flex-1 text-[15px] text-warm-800 dark:text-warm-100">{{ t("phone.addCreature") }}</span>
    </button>
    <button type="button" :class="ROW" data-test="phone-menu-add-channel" @click="ctx.openAdd('channel')">
      <span class="i-carbon-flow-stream text-lg text-warm-500 shrink-0" /><span class="flex-1 text-[15px] text-warm-800 dark:text-warm-100">{{ t("phone.addChannel") }}</span>
    </button>
    <button type="button" :class="ROW" data-test="phone-menu-stop" @click="stop">
      <span class="i-carbon-stop-filled-alt text-lg text-coral shrink-0" /><span class="flex-1 text-[15px] text-coral">{{ t("phone.stop") }}</span>
    </button>
    <div class="h-2" />
  </PhoneSheet>
</template>

<script setup>
import { computed } from "vue"

import { creatureOfTab, modelOfCreature, phoneTools } from "@/components/session-v2/model/phone/phoneModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useSessionDrives } from "@/components/session-v2/model/useSessionDrives"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import PhoneSheet from "@/components/session-v2/phone/PhoneSheet.vue"
import { useCanvasStore } from "@/stores/canvas"

/**
 * The phone session menu (the desktop dock and bar, as one list): the open
 * conversation's model, compact and clear; every tool, live ones counted;
 * add a creature or channel; stop the session.
 */
const emit = defineEmits(["stop"])

const HEAD = "px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-warm-500"
const ROW = "w-full min-h-12 flex items-center gap-3 px-4 py-2 text-left active:bg-warm-200/60 dark:active:bg-warm-800 disabled:opacity-40"

const ctx = useSessionV2()
const t = useV2T()
const canvas = useCanvasStore()
const { store: drives } = useSessionDrives(ctx, { poll: false, kept: true })

const sessionName = computed(() => ctx.instance.value?.config_name || ctx.instanceId.value)
const conversationKey = computed(() => (ctx.tab.value === "chat" ? ctx.chat.activeTab || "" : ""))
const agentName = computed(() => creatureOfTab(ctx.instance.value, conversationKey.value, ctx.chat._rootSourceName))
const model = computed(() => modelOfCreature(ctx.instance.value, ctx.chat, agentName.value))
const actions = computed(() => ctx.column.actions.value)
const tools = computed(() => phoneTools({ drives: drives.order.length, jobs: Object.keys(ctx.chat.runningJobs || {}).length, artifacts: canvas.artifacts.length }))

function run(name) {
  ctx.closeSheet()
  actions.value?.[name]?.()
}

function openTool(tool) {
  if (tool.target === "side") ctx.openSide(tool.id)
  else ctx.openWidget(tool.id)
}

function stop() {
  ctx.closeSheet()
  emit("stop")
}
</script>
