<template>
  <el-dialog v-model="visible" :title="modalTitle" width="480px" :close-on-click-modal="!running" :close-on-press-escape="!running" :show-close="!running" @close="onClose">
    <div class="space-y-3 text-[13px]">
      <div>
        <div class="font-medium mb-1">{{ t("memoryBuild.embedderLabel") }}</div>
        <el-radio-group v-model="form.embedder" :disabled="running" class="flex flex-col gap-1">
          <el-radio value="auto">{{ t("memoryBuild.embedderAuto") }}</el-radio>
          <el-radio value="model2vec">{{ t("memoryBuild.embedderModel2vec") }}</el-radio>
          <el-radio value="sentence-transformer">{{ t("memoryBuild.embedderSentenceTransformer") }}</el-radio>
        </el-radio-group>
      </div>

      <div class="flex gap-2">
        <el-input v-model="form.model" :placeholder="t('memoryBuild.modelPlaceholder')" size="small" :disabled="running" />
        <el-input-number v-model="form.dimensions" :placeholder="t('memoryBuild.dimensionsPlaceholder')" size="small" :disabled="running" :min="1" :controls="false" />
      </div>

      <div v-if="rebuild" class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/20 p-2 text-[12px]">
        {{ t("memoryBuild.rebuildWarning") }}
      </div>

      <div v-if="running || terminal" class="space-y-2 pt-2 border-t border-warm-200 dark:border-warm-700">
        <div class="flex items-center justify-between text-[12px] text-secondary">
          <span class="font-mono">{{ progress.phase || "—" }}</span>
          <span class="font-mono">{{ progress.blocks_indexed || 0 }} / {{ progress.blocks_total || 0 }} {{ t("memoryBuild.events") }}</span>
        </div>
        <el-progress :percentage="Math.min(100, Math.max(0, progress.percent || 0))" :status="progressStatus" />
        <p v-if="progress.agent" class="text-[11px] text-warm-400 font-mono">{{ t("memoryBuild.workingOn") }} {{ progress.agent }}</p>
      </div>

      <p v-if="error" class="text-[12px] text-red-500">{{ error }}</p>
      <p v-if="terminal === 'ok'" class="text-[12px] text-iolite">
        {{ t("memoryBuild.successMsg", { blocks: indexedBlocks }) }}
      </p>
      <p v-if="terminal === 'cancelled'" class="text-[12px] text-warm-500">{{ t("memoryBuild.cancelled") }}</p>
    </div>

    <template #footer>
      <el-button v-if="!running && !terminal" @click="visible = false">{{ t("common.cancel") }}</el-button>
      <el-button v-if="!running && !terminal" type="primary" @click="start">{{ rebuild ? t("memoryBuild.rebuild") : t("memoryBuild.build") }}</el-button>
      <el-button v-if="running" type="danger" plain @click="cancel">{{ t("memoryBuild.cancelRunning") }}</el-button>
      <el-button v-if="terminal" type="primary" @click="visible = false">{{ t("common.close") }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"

import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  sessionName: { type: String, required: true },
  rebuild: { type: Boolean, default: false },
})
const emit = defineEmits(["update:modelValue", "completed"])

const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const form = reactive({
  embedder: "auto",
  model: null,
  dimensions: null,
})

const progress = reactive({
  phase: "",
  percent: 0,
  blocks_indexed: 0,
  blocks_total: 0,
  agent: "",
})

const running = ref(false)
const terminal = ref(null) // null | "ok" | "failed" | "cancelled"
const error = ref("")
const indexedBlocks = ref(0)
let ws = null

const modalTitle = computed(() => (props.rebuild ? t("memoryBuild.titleRebuild", { name: props.sessionName }) : t("memoryBuild.titleBuild", { name: props.sessionName })))

const progressStatus = computed(() => {
  if (terminal.value === "ok") return "success"
  if (terminal.value === "failed") return "exception"
  if (terminal.value === "cancelled") return "warning"
  return null
})

function resetState() {
  progress.phase = ""
  progress.percent = 0
  progress.blocks_indexed = 0
  progress.blocks_total = 0
  progress.agent = ""
  running.value = false
  terminal.value = null
  error.value = ""
  indexedBlocks.value = 0
}

watch(visible, (v) => {
  if (v) resetState()
})

async function start() {
  resetState()
  running.value = true
  try {
    await sessionAPI.buildMemory(props.sessionName, {
      embedder: form.embedder,
      model: form.model || null,
      dimensions: form.dimensions || null,
      force: props.rebuild,
    })
  } catch (e) {
    running.value = false
    terminal.value = "failed"
    error.value = e?.response?.data?.detail || e.message || "build request failed"
    return
  }
  ws = sessionAPI.openMemoryBuildStream(props.sessionName, {
    embedder: form.embedder,
    model: form.model || null,
    dimensions: form.dimensions || null,
    force: props.rebuild,
    onFrame: (frame) => {
      if (frame.status) {
        terminal.value = frame.status
        running.value = false
        if (frame.status === "failed") {
          error.value = frame.error || "build failed"
        } else if (frame.status === "ok") {
          progress.percent = 100
          // Sum blocks across agents for the success message.
          const stats = frame.stats || {}
          indexedBlocks.value = Number(stats.vec_blocks || 0)
          emit("completed", frame)
        }
        return
      }
      progress.phase = frame.phase || progress.phase
      progress.percent = frame.percent ?? progress.percent
      progress.blocks_indexed = frame.blocks_indexed ?? progress.blocks_indexed
      progress.blocks_total = frame.blocks_total ?? progress.blocks_total
      progress.agent = frame.agent ?? progress.agent
    },
    onClose: () => {
      ws = null
      // If the socket closed without a terminal frame, assume failure.
      if (running.value) {
        running.value = false
        terminal.value = "failed"
        if (!error.value) error.value = t("memoryBuild.disconnected")
      }
    },
    onError: () => {
      // Surfaced by onClose terminal path.
    },
  })
}

function cancel() {
  if (ws) {
    try {
      ws.close()
    } catch {
      // already closed
    }
    ws = null
  }
  terminal.value = "cancelled"
  running.value = false
}

function onClose() {
  if (ws) {
    try {
      ws.close()
    } catch {
      // already closed
    }
    ws = null
  }
}
</script>
