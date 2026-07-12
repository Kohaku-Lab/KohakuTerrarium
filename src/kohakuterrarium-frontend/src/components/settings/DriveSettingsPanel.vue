<template>
  <div class="drive-settings flex flex-col gap-3 max-w-3xl">
    <p class="text-xs text-warm-400">The Drive runtime lets creatures hold long-lived commitments that outlive a single turn. This controls the runtime and which installed Drive registrations are enabled on a node — the live Drive records are managed in the workspace Drives panel.</p>

    <!-- Target node (self-hides in standalone mode) -->
    <div class="flex items-center gap-2">
      <SitePicker :model-value="store.node" label="Target node" @update:model-value="onNodeChange" />
      <span v-if="store.node && store.node !== '_host'" class="text-[11px] text-amber-shadow dark:text-amber-light"> Availability + tuning apply to this node's own config. </span>
    </div>

    <div v-if="store.loading" class="text-warm-400 text-sm py-6 text-center">Loading Drive settings…</div>
    <div v-else-if="store.error" class="card p-4 border-l-3 border-l-coral">
      <p class="text-sm text-warm-600 dark:text-warm-400">{{ store.error }}</p>
      <el-button size="small" class="mt-2" @click="store.load()">Retry</el-button>
    </div>

    <template v-else-if="store.draft">
      <!-- Malformed drive-settings.yaml -->
      <div v-if="store.parseError" class="card p-3 border-l-3 border-l-coral">
        <div class="flex items-center gap-2 text-coral text-sm font-medium"><span class="i-carbon-warning-alt" /> Settings file could not be parsed</div>
        <p class="text-[11px] text-warm-500 mt-1 font-mono">{{ store.parseError }}</p>
        <p class="text-[11px] text-warm-400 mt-1">Editing below starts from defaults; saving overwrites the broken file.</p>
      </div>

      <!-- Stale-revision conflict (409) -->
      <div v-if="store.conflict" class="card p-3 border-l-3 border-l-amber">
        <div class="flex items-center gap-2 text-amber-shadow dark:text-amber-light text-sm font-medium"><span class="i-carbon-warning" /> {{ store.conflict.message }}</div>
        <p class="text-[11px] text-warm-500 mt-1">
          On-disk revision is now <span class="font-mono">{{ shortRev(store.conflict.serverRevision) }}</span
          >. Your edits are kept — adopt the server copy to compare, then re-apply your changes.
        </p>
        <div class="flex gap-2 mt-2">
          <el-button size="small" @click="store.adoptServerConflict()">Load server copy</el-button>
          <el-button size="small" text @click="store.conflict = null">Dismiss</el-button>
        </div>
      </div>

      <!-- Dismissing the banner does not resolve the conflict: the draft is
           still stale against disk and Save stays disabled until reload/adopt
           (R1-36). This persistent note explains why. -->
      <div v-else-if="store.staleConflict" class="card p-3 border-l-3 border-l-amber">
        <div class="text-[11px] text-amber-shadow dark:text-amber-light flex items-start gap-1">
          <span class="i-carbon-warning mt-0.5 shrink-0" />
          <span>Settings changed on disk — reload or adopt the server copy before saving. Your edits are kept.</span>
        </div>
      </div>

      <div v-if="store.saveDurability === 'file_only'" class="card p-3 border-l-3 border-l-amber">
        <div class="text-[11px] text-amber-shadow dark:text-amber-light flex items-start gap-1">
          <span class="i-carbon-warning mt-0.5 shrink-0" />
          <span>Settings were saved, but the directory rename barrier was unavailable. File contents are durable; the directory entry has best-effort crash durability.</span>
        </div>
      </div>

      <!-- Runtime enable + summary -->
      <div class="card p-4 flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <div>
            <div class="font-medium text-warm-700 dark:text-warm-300 text-sm">Drive runtime</div>
            <div class="text-[11px] text-warm-400">
              {{ store.runtimeEnabled ? "Enabled — creatures on this node can hold Drives." : "Disabled — no Drive manager, tools, or dispatcher run." }}
            </div>
          </div>
          <el-switch :model-value="store.runtimeEnabled" @change="store.setRuntimeEnabled($event)" />
        </div>

        <!-- Running snapshot (saved != running) -->
        <div v-if="store.runtimeStatus" class="text-[11px] text-warm-500 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-warm-100 dark:border-warm-800 pt-2">
          <span>
            Running:
            <span :class="store.runtimeStatus.enabled ? 'text-aquamarine' : 'text-warm-400'">
              {{ store.runtimeStatus.enabled ? "on" : "off" }}
            </span>
          </span>
          <span v-if="store.runtimeStatus.durability"
            >durability: <span class="font-mono">{{ store.runtimeStatus.durability }}</span></span
          >
          <span v-if="runningCounts">records: {{ runningCounts }}</span>
          <span v-if="store.runtimeStatus.running_revision" class="font-mono">rev {{ shortRev(store.runtimeStatus.running_revision) }}</span>
        </div>
      </div>

      <!-- Runtime tuning (only meaningful when enabled) -->
      <el-collapse v-if="store.runtimeEnabled" class="tuning">
        <el-collapse-item title="Scheduler & limits" name="limits">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <NumberField v-model="store.draft.runtime.dispatcher_concurrency" label="Dispatcher concurrency" :min="1" />
            <NumberField v-model="store.draft.runtime.max_active_per_creature" label="Max active per creature" :min="1" />
            <NumberField v-model="store.draft.runtime.max_pending_per_graph" label="Max pending per graph" :min="1" />
            <NumberField v-model="store.draft.runtime.max_consecutive_drive_turns" label="Max consecutive Drive turns" :min="1" />
          </div>
        </el-collapse-item>
        <el-collapse-item title="Retry & backoff" name="retry">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <NumberField v-model="store.draft.runtime.retry.max_attempts" label="Max attempts" :min="1" />
            <NumberField v-model="store.draft.runtime.retry.initial_backoff_s" label="Initial backoff (s)" :min="0" :step="0.5" />
            <NumberField v-model="store.draft.runtime.retry.max_backoff_s" label="Max backoff (s)" :min="0" :step="1" />
            <NumberField v-model="store.draft.runtime.retry.jitter" label="Jitter (0–1)" :min="0" :max="1" :step="0.05" />
          </div>
        </el-collapse-item>
        <el-collapse-item title="Retention" name="retention">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <NumberField v-model="store.draft.runtime.retention.terminal_days" label="Terminal (days)" :min="0" />
            <NumberField v-model="store.draft.runtime.retention.dead_letter_days" label="Dead-letter (days)" :min="0" />
            <NumberField v-model="store.draft.runtime.retention.acknowledged_delivery_days" label="Ack'd delivery (days)" :min="0" />
            <NumberField v-model="store.draft.runtime.retention.superseded_delivery_days" label="Superseded delivery (days)" :min="0" />
            <NumberField v-model="store.draft.runtime.retention.progress_max_count" label="Progress max count" :min="1" />
            <NumberField v-model="store.draft.runtime.retention.progress_max_age_days" label="Progress max age (days)" :min="0" />
          </div>
        </el-collapse-item>
        <el-collapse-item title="Payload byte budgets" name="budgets">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <NumberField v-model="store.draft.runtime.spec_max_bytes" label="Spec max bytes" :min="1" :step="1024" />
            <NumberField v-model="store.draft.runtime.presentation_max_bytes" label="Presentation max bytes" :min="1" :step="1024" />
            <NumberField v-model="store.draft.runtime.metadata_max_bytes" label="Metadata max bytes" :min="1" :step="1024" />
            <NumberField v-model="store.draft.runtime.evidence_max_bytes" label="Evidence max bytes" :min="1" :step="1024" />
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- Registrations -->
      <div class="flex flex-col gap-2">
        <div class="flex items-center justify-between">
          <div class="font-medium text-warm-700 dark:text-warm-300 text-sm">
            Registrations
            <span class="text-[11px] text-warm-400 font-normal"> ({{ store.enabledCount }} enabled / {{ store.registrationView.length }} available) </span>
          </div>
        </div>
        <div v-if="store.registrationView.length === 0" class="text-[11px] text-warm-400 italic py-3 text-center">No Drive registrations installed on this node. Install a package that provides one, or enable the builtin generic kind.</div>
        <DriveRegistrationCard v-for="reg in store.registrationView" :key="reg.name" :reg="reg" :disabled="store.saving" @toggle="store.toggleRegistration" @update-option="store.setRegistrationOption" @set-options="onSetOptions" />
        <div v-if="store.runtimeEnabled && store.enabledCount === 0" class="text-[11px] text-amber-shadow dark:text-amber-light flex items-center gap-1"><span class="i-carbon-warning" /> The runtime is enabled but no registration is — saving will be rejected. Enable at least one kind.</div>
      </div>

      <!-- Active-record warning for pending disables -->
      <div v-if="store.pendingDisables.length" class="card p-3 border-l-3 border-l-amber">
        <div class="text-[11px] text-amber-shadow dark:text-amber-light flex items-start gap-1">
          <span class="i-carbon-warning mt-0.5 shrink-0" />
          <span>
            Disabling <span class="font-mono">{{ store.pendingDisables.join(", ") }}</span> may leave active Drive records unservable. Review them in the
            <button type="button" class="underline hover:text-iolite" @click="$emit('open-drives')">Drives panel</button>
            before applying.
          </span>
        </div>
      </div>

      <!-- Validation error -->
      <div v-if="store.validationError" class="card p-3 border-l-3 border-l-coral">
        <div class="text-[11px] text-coral flex items-start gap-1">
          <span class="i-carbon-error mt-0.5 shrink-0" />
          <span>{{ store.validationError }}</span>
        </div>
      </div>

      <!-- Apply result (explicit applied_live / restart_required / rejected) -->
      <div v-if="store.applyResult" class="card p-3" :class="applyBorder">
        <div class="flex items-center gap-2 text-sm font-medium" :class="applyText">
          <span :class="applyIcon" />
          {{ applyLabel }}
        </div>
        <div v-if="applyRevs" class="text-[11px] text-warm-500 mt-1 font-mono">{{ applyRevs }}</div>
        <ul v-if="store.applyResult.warnings?.length" class="text-[11px] text-warm-500 mt-1 list-disc list-inside">
          <li v-for="(w, i) in store.applyResult.warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <!-- Footer actions -->
      <div class="flex items-center gap-2 sticky bottom-0 bg-warm-50 dark:bg-warm-900 py-2 border-t border-warm-100 dark:border-warm-800">
        <span v-if="store.dirty" class="text-[11px] text-amber-shadow dark:text-amber-light flex items-center gap-1"> <span class="w-1.5 h-1.5 rounded-full bg-amber" /> Unsaved changes </span>
        <span v-else class="text-[11px] text-warm-400">
          Saved <span class="font-mono">rev {{ shortRev(store.savedRevision) }}</span>
        </span>
        <span class="flex-1" />
        <el-button size="small" :disabled="!store.dirty || store.validating" :loading="store.validating" @click="onValidate">Validate</el-button>
        <el-button size="small" :disabled="!store.dirty" text @click="store.reset()">Discard</el-button>
        <el-button size="small" type="primary" :disabled="!store.canSave || store.saving" :loading="store.saving" @click="onSave">Save</el-button>
        <el-button size="small" type="success" plain :disabled="store.applying" :loading="store.applying" :title="store.dirty ? 'Save first — apply operates on the saved file' : ''" @click="onApply">Apply</el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue"
import { ElMessage } from "element-plus"

import DriveRegistrationCard from "@/components/settings/DriveRegistrationCard.vue"
import NumberField from "@/components/settings/DriveNumberField.vue"
import SitePicker from "@/components/cluster/SitePicker.vue"
import { useDriveSettingsStore } from "@/stores/driveSettings"

defineEmits(["open-drives"])

const store = useDriveSettingsStore()

onMounted(() => {
  if (!store.draft && !store.loading) store.load()
})

function onNodeChange(node) {
  store.setNode(node)
}

function shortRev(rev) {
  if (!rev) return "—"
  return String(rev).slice(0, 8)
}

const runningCounts = computed(() => {
  const counts = store.runtimeStatus?.counts || {}
  const parts = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([status, n]) => `${n} ${status}`)
  return parts.length ? parts.join(", ") : ""
})

function onSetOptions(name, options) {
  if (!store.draft) return
  const regs = store.draft.registrations
  const current = regs[name] || { enabled: false, options: {} }
  regs[name] = { ...current, options }
}

async function onValidate() {
  const res = await store.validate()
  if (res.ok) ElMessage.success("Settings are valid.")
}

async function onSave() {
  const res = await store.save()
  if (res.ok) ElMessage.success("Drive settings saved.")
  else if (res.conflict) ElMessage.warning("Settings changed on disk — compare and retry.")
  else if (res.detail) ElMessage.error(res.detail)
}

async function onApply() {
  if (store.dirty) {
    ElMessage.warning("Save your changes first — apply operates on the saved file.")
    return
  }
  const res = await store.apply()
  if (res.result === "applied_live") ElMessage.success("Applied to the running runtime.")
  else if (res.result === "restart_required") ElMessage.info("Saved — a restart is required to apply.")
  else ElMessage.error("Apply was rejected.")
}

const applyLabel = computed(() => {
  switch (store.applyResult?.result) {
    case "applied_live":
      return "Applied to the live runtime"
    case "restart_required":
      return "Restart required to apply"
    default:
      return "Apply rejected"
  }
})
const applyBorder = computed(() => {
  switch (store.applyResult?.result) {
    case "applied_live":
      return "border-l-3 border-l-aquamarine"
    case "restart_required":
      return "border-l-3 border-l-amber"
    default:
      return "border-l-3 border-l-coral"
  }
})
const applyText = computed(() => {
  switch (store.applyResult?.result) {
    case "applied_live":
      return "text-aquamarine"
    case "restart_required":
      return "text-amber-shadow dark:text-amber-light"
    default:
      return "text-coral"
  }
})
const applyIcon = computed(() => {
  switch (store.applyResult?.result) {
    case "applied_live":
      return "i-carbon-checkmark-filled"
    case "restart_required":
      return "i-carbon-restart"
    default:
      return "i-carbon-error"
  }
})
const applyRevs = computed(() => {
  const r = store.applyResult
  if (!r) return ""
  const parts = []
  if (r.desired_revision) parts.push(`desired ${shortRev(r.desired_revision)}`)
  if (r.running_revision) parts.push(`running ${shortRev(r.running_revision)}`)
  return parts.join(" · ")
})
</script>

<style scoped>
.tuning :deep(.el-collapse-item__content) {
  padding-bottom: 0.75rem;
}
</style>
