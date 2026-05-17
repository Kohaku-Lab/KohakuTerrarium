<template>
  <el-dialog v-model="visible" :title="t('settings.mcp.editTitle', { name: server?.name || '' })" width="560px" @close="onClose">
    <div v-if="server" class="space-y-3 text-[13px]">
      <div>
        <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.name") }}</label>
        <el-input :model-value="server.name" size="small" disabled />
        <p class="text-[11px] text-warm-400 mt-1">{{ t("settings.mcp.nameImmutable") }}</p>
      </div>

      <div>
        <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.transport") }}</label>
        <el-select v-model="form.transport" size="small" class="w-full">
          <el-option value="stdio" :label="t('settings.mcp.transportStdio')" />
          <el-option value="http" :label="t('settings.mcp.transportHttp')" />
        </el-select>
      </div>

      <template v-if="form.transport === 'stdio'">
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.command") }}</label>
          <el-input v-model="form.command" size="small" placeholder="npx" />
        </div>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.args") }}</label>
          <el-input v-model="form.argsStr" size="small" placeholder="-y @modelcontextprotocol/server-filesystem ./" />
          <p class="text-[11px] text-warm-400 mt-1">{{ t("settings.mcp.argsHint") }}</p>
        </div>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.env") }}</label>
          <el-input v-model="form.envStr" size="small" type="textarea" :rows="3" placeholder="KEY=value" />
          <p class="text-[11px] text-warm-400 mt-1">{{ t("settings.mcp.envHint") }}</p>
        </div>
      </template>

      <template v-else>
        <div>
          <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.url") }}</label>
          <el-input v-model="form.url" size="small" placeholder="https://mcp.example.com/api" />
        </div>
      </template>

      <div>
        <label class="text-[11px] text-warm-400 block mb-1">{{ t("settings.mcp.connectTimeout") }}</label>
        <el-input-number v-model="form.connectTimeout" size="small" :min="0" :step="1" :controls="false" :placeholder="t('settings.mcp.connectTimeoutPlaceholder')" />
      </div>

      <!-- Test connection block -->
      <div class="pt-2 border-t border-warm-200 dark:border-warm-700">
        <div class="flex items-center gap-2">
          <el-button size="small" :loading="testing" plain @click="testConnection">
            <span class="i-carbon-flash mr-1" />
            {{ t("settings.mcp.testConnection") }}
          </el-button>
          <span v-if="testResult && testResult.ok" class="text-[12px] text-iolite">
            <span class="i-carbon-checkmark-filled mr-1 align-middle" />
            {{ t("settings.mcp.testOk", { tools: testResult.tool_count, ms: testResult.elapsed_ms }) }}
          </span>
          <span v-else-if="testResult && !testResult.ok" class="text-[12px] text-coral">
            <span class="i-carbon-warning-alt-filled mr-1 align-middle" />
            {{ testResult.error || t("settings.mcp.testFailed") }}
          </span>
        </div>
      </div>

      <!-- Usage block -->
      <div class="pt-2 border-t border-warm-200 dark:border-warm-700">
        <div class="flex items-center justify-between">
          <span class="text-[12px] text-warm-700 dark:text-warm-300 font-medium">
            {{ t("settings.mcp.usedByN", { n: usage.length }) }}
          </span>
          <el-button size="small" plain :loading="loadingUsage" @click="loadUsage">
            <span class="i-carbon-renew mr-1" />
            {{ t("common.refresh") }}
          </el-button>
        </div>
        <ul v-if="usage.length" class="mt-2 space-y-1 text-[12px] font-mono">
          <li v-for="u in usage" :key="u.path" class="text-warm-500 dark:text-warm-400 truncate">
            <span v-if="u.kind === 'creature'" class="i-carbon-bot mr-1 align-middle" />
            <span v-else class="i-carbon-network-4 mr-1 align-middle" />
            {{ u.name }} <span class="text-warm-400">— {{ u.path }}</span>
          </li>
        </ul>
        <p v-else-if="!loadingUsage" class="mt-2 text-[12px] text-warm-400">{{ t("settings.mcp.usageNone") }}</p>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">{{ t("common.cancel") }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ t("common.save") }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  server: { type: Object, default: null },
})
const emit = defineEmits(["update:modelValue", "saved"])
const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const form = reactive({
  transport: "stdio",
  command: "",
  argsStr: "",
  envStr: "",
  url: "",
  connectTimeout: null,
})

const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const usage = ref([])
const loadingUsage = ref(false)

function envObjectToStr(env) {
  if (!env || typeof env !== "object") return ""
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n")
}

function envStrToObject(text) {
  const out = {}
  for (const line of String(text || "").split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith("#")) continue
    const eq = trimmed.indexOf("=")
    if (eq <= 0) continue
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1)
  }
  return out
}

function reset(srv) {
  if (!srv) return
  form.transport = srv.transport || "stdio"
  form.command = srv.command || ""
  form.argsStr = (srv.args || []).join(" ")
  form.envStr = envObjectToStr(srv.env)
  form.url = srv.url || ""
  form.connectTimeout = srv.connect_timeout ?? null
  testResult.value = null
  usage.value = []
}

watch(
  () => [visible.value, props.server],
  ([v, srv]) => {
    if (v && srv) {
      reset(srv)
      loadUsage()
    }
  },
  { immediate: true },
)

async function save() {
  if (!props.server) return
  saving.value = true
  try {
    const patch = {
      transport: form.transport,
    }
    if (form.transport === "stdio") {
      patch.command = form.command
      patch.args = form.argsStr ? form.argsStr.split(/\s+/).filter(Boolean) : []
      patch.env = envStrToObject(form.envStr)
      patch.url = ""
    } else {
      patch.url = form.url
      patch.command = ""
      patch.args = []
      patch.env = {}
    }
    patch.connect_timeout = form.connectTimeout || null
    await settingsAPI.patchMCP(props.server.name, patch)
    ElMessage.success(t("settings.mcp.saved", { name: props.server.name }))
    emit("saved")
    visible.value = false
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || t("settings.mcp.saveFailed"))
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  if (!props.server) return
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await settingsAPI.testMCP(props.server.name)
  } catch (err) {
    testResult.value = {
      ok: false,
      error: err?.response?.data?.detail || err.message || "test failed",
    }
  } finally {
    testing.value = false
  }
}

async function loadUsage() {
  if (!props.server) return
  loadingUsage.value = true
  try {
    usage.value = await settingsAPI.mcpUsage(props.server.name)
  } catch {
    usage.value = []
  } finally {
    loadingUsage.value = false
  }
}

function onClose() {
  testResult.value = null
}
</script>
