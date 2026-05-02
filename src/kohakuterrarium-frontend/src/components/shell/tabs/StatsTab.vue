<template>
  <div class="h-full overflow-y-auto bg-warm-50 dark:bg-warm-950">
    <div class="max-w-5xl mx-auto py-6 px-6 space-y-6">
      <!-- Header -->
      <header class="flex items-end justify-between">
        <div>
          <h1 class="text-2xl font-bold text-warm-800 dark:text-warm-200">{{ t("shell.stats.title") }}</h1>
          <p class="text-sm text-warm-500 mt-1">{{ t("shell.stats.subtitle") }}</p>
        </div>
        <button class="text-xs px-3 py-1.5 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite text-warm-600 dark:text-warm-400 flex items-center gap-1.5" :disabled="loading" @click="refreshAll">
          <span class="i-carbon-renew" :class="loading ? 'animate-spin' : ''" />
          {{ t("shell.stats.refresh") }}
        </button>
      </header>

      <!-- ── Storage ─────────────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.storage") }}</h2>
        <div v-if="diskLoading" class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
        <div v-else class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Metric label="Saved sessions" :value="String(disk.count ?? 0)" />
          <Metric label="Total on disk" :value="formatBytes(disk.total_bytes ?? 0)" />
          <Metric label="Oldest" :value="formatRelative(disk.oldest_at)" />
          <Metric label="Newest" :value="formatRelative(disk.newest_at)" />
        </div>
        <div v-if="disk.session_dir" class="text-[10px] font-mono text-warm-400 mt-3 truncate" :title="disk.session_dir">
          {{ disk.session_dir }}
        </div>
      </section>

      <!-- ── Sessions overview ────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.sessionsOverview") }}</h2>
        <div v-if="sessionStatsLoading" class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
        <template v-else>
          <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Metric label="Last 24h" :value="String(sessionStats.by_recency?.['1d'] ?? 0)" />
            <Metric label="Last 7d" :value="String(sessionStats.by_recency?.['7d'] ?? 0)" />
            <Metric label="Last 30d" :value="String(sessionStats.by_recency?.['30d'] ?? 0)" />
            <Metric label="Older" :value="String(sessionStats.by_recency?.['older'] ?? 0)" />
          </div>
          <div class="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-6 text-xs">
            <div>
              <div class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">By type</div>
              <ul class="space-y-1">
                <li v-for="(n, k) in sessionStats.by_config_type" :key="k" class="flex items-center justify-between">
                  <span class="text-warm-700 dark:text-warm-300">{{ k || "unknown" }}</span>
                  <span class="font-mono text-warm-500">{{ n }}</span>
                </li>
              </ul>
            </div>
            <div>
              <div class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">By status</div>
              <ul class="space-y-1">
                <li v-for="(n, k) in sessionStats.by_status" :key="k" class="flex items-center justify-between">
                  <span class="text-warm-700 dark:text-warm-300">{{ k || "unknown" }}</span>
                  <span class="font-mono text-warm-500">{{ n }}</span>
                </li>
              </ul>
            </div>
            <div>
              <div class="text-[10px] uppercase tracking-wider text-warm-500 mb-1.5">Top agents</div>
              <ul v-if="sessionStats.agents_top?.length" class="space-y-1">
                <li v-for="[name, n] in sessionStats.agents_top" :key="name" class="flex items-center justify-between">
                  <span class="text-warm-700 dark:text-warm-300 truncate">{{ name }}</span>
                  <span class="font-mono text-warm-500">{{ n }}</span>
                </li>
              </ul>
              <div v-else class="text-warm-400 italic">no data</div>
            </div>
          </div>
          <div v-if="sessionStats.average_age_seconds != null" class="text-[10px] text-warm-400 mt-4">Average age: {{ formatDuration(sessionStats.average_age_seconds) }}</div>
        </template>
      </section>

      <!-- ── Live activity ─────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.liveActivity") }}</h2>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Metric label="Running" :value="String(running)" />
          <Metric label="Creatures" :value="String(creatureCount)" />
          <Metric label="Terrariums" :value="String(terrariumCount)" />
          <Metric label="Tools / sub-agents in flight" :value="String(jobsInFlight)" />
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
          <Metric label="Tokens (prompt)" :value="formatNum(tokens.prompt)" />
          <Metric label="Tokens (completion)" :value="formatNum(tokens.completion)" />
          <Metric label="Tokens (cached)" :value="formatNum(tokens.cached)" />
          <Metric label="Context %" :value="contextPercent + '%'" />
        </div>
        <div class="text-[10px] text-warm-400 mt-3 italic">Token totals reflect whichever creature chat is currently bound to; process-wide aggregation arrives with the metrics module.</div>
      </section>

      <!-- ── Workspace ─────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.workspace") }}</h2>
        <div v-if="workspaceLoading" class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
        <div v-else class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Metric label="Local creatures" :value="String(workspace.creatures)" />
          <Metric label="Local terrariums" :value="String(workspace.terrariums)" />
          <Metric label="LLM presets" :value="String(workspace.profiles ?? '?')" />
          <Metric label="LLM backends" :value="String(workspace.backends ?? '?')" />
        </div>
      </section>

      <!-- ── Providers ─────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.providers") }}</h2>
        <div v-if="providersLoading" class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
        <div v-else-if="providers.length === 0" class="text-warm-400 italic text-sm py-2">No providers configured. Add an API key or sign in via Settings → Providers.</div>
        <ul v-else class="space-y-1.5 text-sm">
          <li v-for="p in providers" :key="p.name" class="flex items-center gap-3 py-1 border-b border-warm-100 dark:border-warm-800/50 last:border-b-0">
            <span class="w-2 h-2 rounded-full" :class="dotForState(p.state)" />
            <span class="font-medium text-warm-700 dark:text-warm-300 w-40 truncate">{{ p.name }}</span>
            <span class="text-xs text-warm-500">{{ stateLabel(p.state) }}</span>
            <span v-if="p.detail" class="text-xs text-warm-400 ml-auto truncate max-w-md">
              {{ p.detail }}
            </span>
          </li>
        </ul>
      </section>

      <!-- ── MCP servers ─────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.mcpServers") }}</h2>
        <div v-if="mcpLoading" class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
        <div v-else-if="mcp.length === 0" class="text-warm-400 italic text-sm py-2">No MCP servers configured.</div>
        <ul v-else class="space-y-1 text-sm">
          <li v-for="m in mcp" :key="m.name" class="flex items-center gap-3 py-0.5">
            <span class="w-1.5 h-1.5 rounded-full bg-warm-400" />
            <span class="font-medium text-warm-700 dark:text-warm-300 w-40 truncate">{{ m.name }}</span>
            <span class="text-xs text-warm-500">{{ m.transport ?? "—" }}</span>
            <span v-if="m.url || m.command" class="text-xs text-warm-400 ml-auto font-mono truncate max-w-md">
              {{ m.url ?? m.command }}
            </span>
          </li>
        </ul>
        <div class="text-[10px] text-warm-400 mt-3 italic">Configured count only. Connection-state probing arrives with the metrics module.</div>
      </section>

      <!-- ── System ─────────────────────────────── -->
      <section class="card p-5">
        <h2 class="text-xs uppercase tracking-wider text-warm-500 mb-3">{{ t("shell.stats.system") }}</h2>
        <dl v-if="serverInfo" class="grid grid-cols-[140px_1fr] gap-y-1 text-sm">
          <dt class="text-warm-500">Platform</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ serverInfo.platform ?? "—" }}</dd>
          <dt class="text-warm-500">Working dir</dt>
          <dd class="font-mono text-xs truncate" :title="serverInfo.cwd">{{ serverInfo.cwd ?? "—" }}</dd>
          <template v-if="serverInfo.python">
            <dt class="text-warm-500">Python</dt>
            <dd class="text-warm-700 dark:text-warm-300">{{ serverInfo.python }}</dd>
          </template>
          <template v-if="serverInfo.version">
            <dt class="text-warm-500">KT version</dt>
            <dd class="text-warm-700 dark:text-warm-300 font-mono">{{ serverInfo.version }}</dd>
          </template>
          <dt class="text-warm-500">UI version</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ uiVersion }}</dd>
        </dl>
        <div v-else class="text-warm-400 italic text-sm">{{ t("shell.stats.loading") }}</div>
      </section>

      <!-- ── Process metrics: live throughput ────── -->
      <section class="card p-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-warm-500">{{ t("shell.stats.liveThroughput") }}</h2>
          <span class="text-[10px] text-warm-400">{{ t("shell.stats.liveThroughputCaveat") }}</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <ThroughputRow :label="t('shell.stats.llmCalls')" :rate="rateFor('llm')" :series="metrics.rates?.llm" />
          <ThroughputRow :label="t('shell.stats.toolCalls')" :rate="rateFor('tool')" :series="metrics.rates?.tool" />
          <ThroughputRow :label="t('shell.stats.subagents')" :rate="rateFor('subagent')" :series="metrics.rates?.subagent" />
          <ThroughputRow :label="t('shell.stats.errors')" :rate="rateFor('error')" :series="metrics.rates?.error" :stroke-color="errorRate > 0 ? '#D46B6B' : '#5A4FCF'" />
        </div>
      </section>

      <!-- ── Process metrics: latency (p50 / p95 over 5 min) ────── -->
      <section class="card p-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-warm-500">{{ t("shell.stats.latency") }}</h2>
          <span class="text-[10px] text-warm-400">{{ t("shell.stats.latencyWindow") }}</span>
        </div>
        <div v-if="latencyRows.length === 0" class="text-warm-400 italic text-xs py-3">{{ t("shell.stats.latencyEmpty") }}</div>
        <table v-else class="w-full text-xs">
          <thead>
            <tr class="text-warm-500">
              <th class="text-left font-medium pb-1.5 pl-1 w-20">Kind</th>
              <th class="text-left font-medium pb-1.5">Label</th>
              <th class="text-right font-medium pb-1.5 w-16">n</th>
              <th class="text-right font-medium pb-1.5 w-20">p50</th>
              <th class="text-right font-medium pb-1.5 w-20 pr-1">p95</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in latencyRows" :key="`${row.kind}|${row.label}`" class="border-t border-warm-100 dark:border-warm-800/50">
              <td class="pl-1 py-1 text-warm-500 uppercase text-[10px] tracking-wider">{{ row.kind }}</td>
              <td class="font-mono truncate text-warm-700 dark:text-warm-300" :title="row.label">{{ row.label }}</td>
              <td class="text-right text-warm-500 font-mono">{{ row.n }}</td>
              <td class="text-right font-mono text-warm-700 dark:text-warm-300">{{ fmtMs(row.p50) }}</td>
              <td class="text-right font-mono pr-1" :class="row.p95 > 5000 ? 'text-coral' : row.p95 > 1500 ? 'text-amber' : 'text-warm-700 dark:text-warm-300'">{{ fmtMs(row.p95) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- ── Process metrics: tokens ────── -->
      <section class="card p-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-warm-500">{{ t("shell.stats.tokens") }}</h2>
          <span class="text-[10px] text-warm-400">{{ t("shell.stats.tokensSinceUptime", { uptime: fmtUptime(metrics.uptime_s) }) }}</span>
        </div>
        <div v-if="tokenRows.length === 0" class="text-warm-400 italic text-xs py-3">{{ t("shell.stats.tokensEmpty") }}</div>
        <table v-else class="w-full text-xs">
          <thead>
            <tr class="text-warm-500">
              <th class="text-left font-medium pb-1.5 pl-1">Provider · model</th>
              <th class="text-right font-medium pb-1.5 w-24">Prompt</th>
              <th class="text-right font-medium pb-1.5 w-24">Completion</th>
              <th class="text-right font-medium pb-1.5 w-24 pr-1">Cache R/W</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in tokenRows" :key="row.label" class="border-t border-warm-100 dark:border-warm-800/50">
              <td class="pl-1 py-1 font-mono text-warm-700 dark:text-warm-300 truncate">{{ row.label }}</td>
              <td class="text-right font-mono text-warm-700 dark:text-warm-300">{{ formatNum(row.prompt) }}</td>
              <td class="text-right font-mono text-warm-700 dark:text-warm-300">{{ formatNum(row.completion) }}</td>
              <td class="text-right font-mono text-warm-500 pr-1">{{ formatNum(row.cache_read) }} / {{ formatNum(row.cache_write) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- ── Process metrics: errors ────── -->
      <section v-if="errorRows.length > 0" class="card p-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-warm-500">{{ t("shell.stats.errorsTitle") }}</h2>
          <span class="text-[10px] text-warm-400">{{ t("shell.stats.errorsAccumulated") }}</span>
        </div>
        <div class="flex flex-wrap gap-2">
          <div v-for="row in errorRows" :key="row.source" class="flex items-baseline gap-1.5 px-2 py-1 rounded bg-coral/10 dark:bg-coral/15 text-coral text-xs">
            <span class="text-[10px] uppercase tracking-wider opacity-80">{{ row.source }}</span>
            <span class="font-mono font-semibold">{{ row.count }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, h, onMounted, onUnmounted, ref } from "vue"

import Metric from "@/components/common/Metric.vue"
import Sparkline from "@/components/common/Sparkline.vue"
import { useInstancesStore } from "@/stores/instances"
import { useStatusStore } from "@/stores/status"
import { configAPI, settingsAPI, statsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { getUIVersion } from "@/utils/uiVersion"

const { t } = useI18n()

// Inline row component for the throughput strip — keeps the scroll
// state of the Stats tab predictable. Defined here rather than as a
// separate file because it has no other consumer.
const ThroughputRow = (props) =>
  h("div", { class: "flex flex-col gap-0.5" }, [
    h("div", { class: "flex items-baseline justify-between" }, [h("span", { class: "text-[10px] uppercase tracking-wider text-warm-500" }, props.label), h("span", { class: "font-mono text-sm text-warm-800 dark:text-warm-200" }, props.rate.toFixed(0) + "/m")]),
    h(Sparkline, {
      values: props.series ?? [],
      width: 160,
      height: 22,
      strokeColor: props.strokeColor,
      fillColor: props.strokeColor,
    }),
  ])
ThroughputRow.props = ["label", "rate", "series", "strokeColor"]

defineProps({ tab: { type: Object, required: true } })

const instances = useInstancesStore()
const status = useStatusStore()

// ── Storage ─────────────────────────────────────────────────────
const disk = ref({})
const diskLoading = ref(true)

async function loadDisk() {
  diskLoading.value = true
  try {
    disk.value = await statsAPI.diskUsage()
  } catch {
    disk.value = {}
  } finally {
    diskLoading.value = false
  }
}

// ── Session stats ──────────────────────────────────────────────
const sessionStats = ref({})
const sessionStatsLoading = ref(true)

async function loadSessionStats() {
  sessionStatsLoading.value = true
  try {
    sessionStats.value = await statsAPI.sessionStats()
  } catch {
    sessionStats.value = {}
  } finally {
    sessionStatsLoading.value = false
  }
}

// ── Workspace ──────────────────────────────────────────────────
const workspace = ref({ creatures: 0, terrariums: 0 })
const workspaceLoading = ref(true)

async function loadWorkspace() {
  workspaceLoading.value = true
  try {
    const [creatures, terrariums, profiles, backends] = await Promise.all([configAPI.listCreatures().catch(() => []), configAPI.listTerrariums().catch(() => []), settingsAPI.getProfiles().catch(() => null), settingsAPI.getBackends().catch(() => null)])
    workspace.value = {
      creatures: Array.isArray(creatures) ? creatures.length : 0,
      terrariums: Array.isArray(terrariums) ? terrariums.length : 0,
      profiles: _countList(profiles, "profiles"),
      backends: _countList(backends, "backends"),
    }
  } finally {
    workspaceLoading.value = false
  }
}

function _countList(resp, key) {
  if (Array.isArray(resp)) return resp.length
  if (resp && Array.isArray(resp[key])) return resp[key].length
  return 0
}

// ── Providers ───────────────────────────────────────────────────
const providers = ref([])
const providersLoading = ref(true)

async function loadProviders() {
  providersLoading.value = true
  try {
    const [backendsResp, keysResp, codexStatus] = await Promise.all([settingsAPI.getBackends(), settingsAPI.getKeys(), settingsAPI.getCodexStatus().catch(() => null)])
    const backendList = Array.isArray(backendsResp) ? backendsResp : (backendsResp?.backends ?? [])
    const keysList = Array.isArray(keysResp) ? keysResp : (keysResp?.providers ?? keysResp?.keys ?? [])
    const keyByProvider = new Map(keysList.map((k) => [k.provider ?? k.name, k]))

    providers.value = backendList.map((b) => {
      const name = b.name ?? b.provider
      const key = keyByProvider.get(name)
      const isCodex = (b.backend_type ?? "").toLowerCase() === "codex"
      let state = "unconfigured"
      let detail = ""
      if (isCodex) {
        if (codexStatus?.authenticated) {
          state = "ok"
          detail = codexStatus?.expires_in ? `OAuth · ${codexStatus.expires_in}` : "OAuth signed in"
        } else {
          state = "unconfigured"
          detail = "OAuth required"
        }
      } else if (b.has_key === true || b.available === true || key) {
        state = "ok"
        detail = key?.masked_key ? `Key: ${key.masked_key}` : b.env_var ? `Env: ${b.env_var}` : "Key configured"
      } else {
        state = "unconfigured"
        detail = b.env_var ? `Set ${b.env_var} or add a key` : "No key set"
      }
      return { name, state, detail, backend_type: b.backend_type }
    })
  } catch {
    providers.value = []
  } finally {
    providersLoading.value = false
  }
}

// ── MCP ─────────────────────────────────────────────────────────
const mcp = ref([])
const mcpLoading = ref(true)

async function loadMcp() {
  mcpLoading.value = true
  try {
    const resp = await settingsAPI.listMCP()
    const list = Array.isArray(resp) ? resp : (resp?.servers ?? resp?.items ?? [])
    mcp.value = Array.isArray(list) ? list : []
  } catch {
    mcp.value = []
  } finally {
    mcpLoading.value = false
  }
}

// ── System info ────────────────────────────────────────────────
const serverInfo = ref(null)
const uiVersion = computed(() => getUIVersion())

async function loadServerInfo() {
  try {
    serverInfo.value = await configAPI.getServerInfo()
  } catch {
    serverInfo.value = null
  }
}

// ── Live activity (derived) ─────────────────────────────────────
const running = computed(() => instances.list.filter((i) => i.status === "running").length)
const creatureCount = computed(() => instances.list.filter((i) => i.type === "creature").length)
const terrariumCount = computed(() => instances.list.filter((i) => i.type === "terrarium").length)
const jobsInFlight = computed(() => status.runningJobs.length)
const tokens = computed(() => ({
  prompt: status.tokenUsage.promptTokens || 0,
  completion: status.tokenUsage.completionTokens || 0,
  cached: status.tokenUsage.cachedTokens || 0,
}))
const contextPercent = computed(() => Math.round(status.tokenUsage.contextPercent || 0))

// ── Process metrics ─────────────────────────────────────────────
const metrics = ref({})

async function loadMetrics() {
  try {
    metrics.value = await statsAPI.metrics()
  } catch {
    // Soft-fail — keep stale data if a poll fails so the UI doesn't
    // flicker between numbers and "Loading…" on a transient blip.
  }
}

const errorRate = computed(() => rateFor("error"))

function rateFor(kind) {
  // 5 s buckets × last 12 buckets = 1 minute window. Multiply count
  // by 60 / 60 to express per-minute (already 1-minute window). The
  // sparkline shows the full 5-min ring; the headline number is the
  // current 1-minute rate so the user feels what's happening *now*.
  const series = metrics.value?.rates?.[kind] || []
  const last12 = series.slice(-12)
  return last12.reduce((a, b) => a + (Number(b) || 0), 0)
}

const latencyRows = computed(() => {
  const out = []
  const hist = metrics.value?.histograms || {}
  const push = (kind, name, key) => {
    const buckets = hist[name]?.[key]?.["5m"]
    if (!buckets || buckets.n === 0) return
    out.push({
      kind,
      label: key.replace(/\|/g, " · "),
      n: buckets.n,
      p50: buckets.p50_ms,
      p95: buckets.p95_ms,
      p99: buckets.p99_ms,
    })
  }
  for (const k of Object.keys(hist.llm_response_ms || {})) push("LLM", "llm_response_ms", k)
  for (const k of Object.keys(hist.tool_exec_ms || {})) push("Tool", "tool_exec_ms", k)
  for (const k of Object.keys(hist.subagent_duration_ms || {})) push("Sub", "subagent_duration_ms", k)
  for (const k of Object.keys(hist.plugin_hook_ms || {})) push("Plugin", "plugin_hook_ms", k)
  // Sort by p95 desc so the slowest things bubble to the top.
  out.sort((a, b) => b.p95 - a.p95)
  return out
})

const tokenRows = computed(() => {
  const tokens = metrics.value?.counters?.tokens_total || {}
  const grouped = {}
  for (const [labels, count] of Object.entries(tokens)) {
    const [provider, model, kind] = labels.split("|")
    const key = `${provider}/${model}`
    grouped[key] = grouped[key] || {
      label: key,
      prompt: 0,
      completion: 0,
      cache_read: 0,
      cache_write: 0,
    }
    if (kind in grouped[key]) grouped[key][kind] = count
  }
  return Object.values(grouped).sort((a, b) => b.prompt + b.completion - (a.prompt + a.completion))
})

const errorRows = computed(() => {
  const errors = metrics.value?.counters?.errors_total || {}
  return Object.entries(errors)
    .map(([source, count]) => ({ source, count }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count)
})

function fmtMs(ms) {
  if (!ms) return "—"
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function fmtUptime(seconds) {
  if (!seconds) return "—"
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

// ── Lifecycle ───────────────────────────────────────────────────
const loading = computed(() => diskLoading.value || providersLoading.value || mcpLoading.value || sessionStatsLoading.value || workspaceLoading.value)

let refreshTimer = null

async function refreshAll() {
  await Promise.all([loadDisk(), loadSessionStats(), loadProviders(), loadMcp(), loadWorkspace(), loadServerInfo(), loadMetrics(), instances.fetchAll()])
}

onMounted(() => {
  refreshAll()
  // Auto-refresh: process metrics + instances + sessionStats every
  // 5 s. Disk is pure stat — refresh on the same cadence so the
  // numbers stay live without a manual click.
  refreshTimer = setInterval(() => {
    instances.fetchAll()
    loadSessionStats()
    loadMetrics()
  }, 5000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

// ── Formatting helpers ──────────────────────────────────────────
function formatBytes(b) {
  if (!b) return "0 B"
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatNum(n) {
  if (!n) return "0"
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "k"
  return (n / 1_000_000).toFixed(2) + "M"
}

function formatRelative(ts) {
  if (!ts) return "—"
  const ms = Date.now() - ts * 1000
  if (ms < 60_000) return "just now"
  const m = Math.floor(ms / 60_000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

function formatDuration(seconds) {
  if (!seconds) return "0s"
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

function dotForState(state) {
  return (
    {
      ok: "bg-aquamarine",
      warning: "bg-amber",
      error: "bg-coral",
      unconfigured: "bg-warm-400",
    }[state] || "bg-warm-400"
  )
}

function stateLabel(state) {
  return (
    {
      ok: "ready",
      warning: "warning",
      error: "error",
      unconfigured: "no auth",
    }[state] || state
  )
}
</script>
