<template>
  <div class="h-full min-h-0 overflow-hidden flex flex-col gap-3 p-4">
    <!-- Picker row -->
    <div class="flex flex-wrap items-center gap-2 text-[12px]">
      <span class="text-warm-400">{{ t("sessionViewer.diff.pickOther") }}:</span>
      <el-select v-model="otherName" filterable size="small" :placeholder="t('sessionViewer.diff.pickOther')" :loading="listLoading" style="min-width: 220px">
        <el-option v-for="s in otherChoices" :key="s.name" :value="s.name" :label="s.name" />
      </el-select>
      <span v-if="agents.length > 1" class="text-warm-400 ml-2">{{ t("sessionViewer.diff.agent") }}:</span>
      <el-select v-if="agents.length > 1" v-model="agent" size="small" clearable style="width: 140px">
        <el-option v-for="a in agents" :key="a" :value="a" :label="a" />
      </el-select>
      <el-button size="small" type="primary" :loading="loading" :disabled="!otherName" @click="run">{{ t("sessionViewer.diff.pickOther") }}</el-button>
    </div>

    <!-- Body -->
    <div class="flex-1 min-h-0 overflow-y-auto">
      <div v-if="error" class="card p-4 text-coral text-sm">{{ error }}</div>
      <div v-else-if="loading" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.diff.loading") }}</div>
      <div v-else-if="!result" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.diff.empty") }}</div>

      <div v-else class="flex flex-col gap-3">
        <!-- Summary -->
        <div class="card p-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-[12px]">
          <Stat :label="t('sessionViewer.diff.shared')" :value="String(result.shared_prefix_length)" />
          <Stat :label="`${result.a.session_name}`" :value="`${result.a.total_messages} msgs`" />
          <Stat :label="`${result.b.session_name}`" :value="`${result.b.total_messages} msgs`" />
          <Stat :label="t('sessionViewer.diff.changed')" :value="result.identical ? '0' : `${result.a_only.length}+${result.b_only.length}`" />
        </div>

        <div v-if="result.identical" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.diff.identical") }}</div>

        <!-- A only -->
        <div v-if="result.a_only.length" class="card p-3 flex flex-col gap-2">
          <div class="text-[11px] uppercase tracking-wider text-aquamarine">
            {{ t("sessionViewer.diff.added") }} <span class="text-warm-400">({{ result.a.session_name }})</span>
          </div>
          <DiffList :rows="result.a_only" tone="add" />
        </div>

        <!-- B only -->
        <div v-if="result.b_only.length" class="card p-3 flex flex-col gap-2">
          <div class="text-[11px] uppercase tracking-wider text-coral">
            {{ t("sessionViewer.diff.removed") }} <span class="text-warm-400">({{ result.b.session_name }})</span>
          </div>
          <DiffList :rows="result.b_only" tone="remove" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, ref, watch } from "vue"

import { useSessionDetailStore } from "@/stores/sessionDetail"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const detail = useSessionDetailStore()

const otherName = ref("")
const agent = ref("")
const sessionsList = ref([])
const listLoading = ref(false)
const loading = ref(false)
const error = ref("")
const result = ref(null)

const agents = computed(() => detail.agents || [])
const otherChoices = computed(() => sessionsList.value.filter((s) => s.name !== detail.name))

async function loadList() {
  listLoading.value = true
  try {
    const data = await sessionAPI.list({ limit: 200 })
    sessionsList.value = data.sessions || []
  } catch (e) {
    error.value = `${t("sessionViewer.diff.failed")}: ${e?.message || e}`
  } finally {
    listLoading.value = false
  }
}

async function run() {
  if (!detail.name || !otherName.value) return
  loading.value = true
  error.value = ""
  result.value = null
  try {
    result.value = await sessionAPI.getDiff(detail.name, otherName.value, agent.value || null)
  } catch (e) {
    error.value = `${t("sessionViewer.diff.failed")}: ${e?.message || e}`
  } finally {
    loading.value = false
  }
}

watch(
  () => detail.name,
  () => {
    result.value = null
    error.value = ""
    if (!sessionsList.value.length) loadList()
  },
  { immediate: true },
)

const Stat = (props) => {
  return h("div", { class: "flex flex-col gap-0.5" }, [h("span", { class: "text-[10px] uppercase tracking-wider text-warm-400 truncate" }, props.label), h("span", { class: "font-medium text-warm-700 dark:text-warm-300 truncate" }, String(props.value ?? "—"))])
}
Stat.props = ["label", "value"]

const DiffList = (props) => {
  const tone = props.tone === "add" ? "border-l-aquamarine bg-aquamarine/5" : "border-l-coral bg-coral/5"
  return h(
    "div",
    { class: "flex flex-col gap-1" },
    props.rows.map((r, i) =>
      h(
        "div",
        {
          key: i,
          class: `text-[12px] px-2 py-1.5 border-l-2 ${tone} flex items-start gap-2`,
        },
        [
          h("span", { class: "font-mono text-warm-500 w-16 shrink-0" }, r.role || "-"),
          h(
            "span",
            {
              class: "flex-1 min-w-0 text-warm-700 dark:text-warm-300 whitespace-pre-wrap break-words",
            },
            r.content_preview || (r.has_tool_calls ? "[tool calls]" : ""),
          ),
        ],
      ),
    ),
  )
}
DiffList.props = ["rows", "tone"]
</script>
