<template>
  <div class="drive-detail h-full flex flex-col min-h-0">
    <!-- Header -->
    <div class="px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <div class="flex items-center gap-2">
        <span :class="[status.icon, TONE_TEXT[status.tone], 'text-base shrink-0']" />
        <span class="font-semibold text-sm text-warm-800 dark:text-warm-100 truncate flex-1">{{ record.title || record.drive_id }}</span>
        <el-tag size="small" effect="plain">{{ record.kind }}</el-tag>
        <button v-if="closable" type="button" class="text-warm-400 hover:text-coral" title="Close" @click="$emit('close')">
          <span class="i-carbon-close" />
        </button>
      </div>
      <div class="flex items-center gap-2 mt-1 text-[11px] text-warm-500">
        <span class="inline-flex items-center gap-1" :class="TONE_TEXT[status.tone]">{{ status.label }}</span>
        <span class="font-mono text-warm-400">{{ record.drive_id }}</span>
        <span class="text-warm-400">· rev {{ record.revision }}</span>
      </div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto px-3 py-2 flex flex-col gap-3 text-[12px]">
      <!-- Attention banner — blocked / failed / orphaned / dead-letter / unavailable -->
      <div v-if="attention" class="rounded border-l-3 border-l-coral bg-coral/10 px-2 py-1.5 flex items-start gap-1.5">
        <span class="i-carbon-warning-alt text-coral mt-0.5 shrink-0" />
        <div class="text-[11px] text-warm-700 dark:text-warm-300">
          <div v-if="record.status === 'blocked'">Blocked{{ record.status_reason ? `: ${record.status_reason}` : "" }} — needs attention before it can proceed.</div>
          <div v-else-if="record.status === 'failed'">Failed{{ record.status_reason ? `: ${record.status_reason}` : "" }}.</div>
          <div v-if="record.assignment_state === 'orphaned'">Assignee is gone — this Drive is orphaned and awaits reassignment.</div>
          <div v-if="availability">{{ availability.label }} — its registration cannot currently serve this kind.</div>
          <div v-if="flags?.deadLetter">A delivery was dead-lettered; inspect the delivery history below.</div>
        </div>
      </div>

      <!-- Pending terminal proposal awaiting verification (off-record state) -->
      <div v-if="pendingProposal" class="rounded border-l-3 border-l-sapphire bg-sapphire/10 px-2 py-1.5 flex items-start gap-1.5">
        <span class="i-carbon-task-view text-sapphire dark:text-sapphire-light mt-0.5 shrink-0" />
        <div class="text-[11px] text-warm-700 dark:text-warm-300">A terminal completion is proposed and awaiting verification.<span v-if="has('verify_terminal')"> Approve or reject below.</span></div>
      </div>

      <!-- Identity / readiness -->
      <section>
        <div class="drive-section-title">Identity & readiness</div>
        <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt class="text-warm-400">Owner</dt>
          <dd class="text-warm-700 dark:text-warm-300">
            {{ actorLabel(record.owner) }} <span class="text-warm-400">({{ record.owner_scope }})</span>
          </dd>
          <dt class="text-warm-400">Assignee</dt>
          <dd class="text-warm-700 dark:text-warm-300">
            {{ record.assignee_creature_id || "unassigned" }} <span v-if="record.assignment_state" class="text-warm-400">· {{ record.assignment_state }}</span>
          </dd>
          <dt class="text-warm-400">Scope</dt>
          <dd class="text-warm-700 dark:text-warm-300">
            {{ record.scope_type }} / <span class="font-mono">{{ record.scope_id }}</span>
          </dd>
          <dt class="text-warm-400">Priority</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ record.priority }}</dd>
          <dt class="text-warm-400">Durability</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ record.durability || "—" }}</dd>
          <dt class="text-warm-400">Availability</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ record.availability || "available" }}</dd>
          <template v-if="record.not_before">
            <dt class="text-warm-400">Not before</dt>
            <dd class="text-warm-700 dark:text-warm-300">{{ relativeTime(record.not_before) }}</dd>
          </template>
          <template v-if="record.expires_at">
            <dt class="text-warm-400">Expires</dt>
            <dd class="text-warm-700 dark:text-warm-300">{{ relativeTime(record.expires_at) }}</dd>
          </template>
          <dt class="text-warm-400">Created</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ actorLabel(record.created_by) }} · {{ relativeTime(record.created_at) }}</dd>
          <dt class="text-warm-400">Updated</dt>
          <dd class="text-warm-700 dark:text-warm-300">{{ relativeTime(record.updated_at) }}</dd>
          <template v-if="record.dependency_ids?.length">
            <dt class="text-warm-400">Depends on</dt>
            <dd class="text-warm-700 dark:text-warm-300 font-mono">{{ record.dependency_ids.join(", ") }}</dd>
          </template>
        </dl>
      </section>

      <!-- Presentation (bounded, user-facing) -->
      <section v-if="hasContent(record.presentation)">
        <div class="drive-section-title">Presentation</div>
        <DriveJsonBlock :value="record.presentation" />
      </section>

      <!-- Spec / evidence — sensitive, collapsed unless revealed -->
      <section>
        <div class="drive-section-title">
          Spec
          <button type="button" class="ml-2 text-[10px] text-warm-500 hover:text-iolite normal-case" @click="showSpec = !showSpec">
            {{ showSpec ? "hide" : "reveal" }}
          </button>
        </div>
        <div v-if="!showSpec" class="text-[11px] text-warm-400 italic">Collapsed — may contain sensitive detail.</div>
        <DriveJsonBlock v-else-if="hasContent(record.spec)" :value="record.spec" />
        <div v-else class="text-[11px] text-warm-400 italic">No spec, or redacted by the server.</div>
      </section>

      <!-- Terminal proposal / verification -->
      <section v-if="record.terminal_evidence">
        <div class="drive-section-title">Terminal evidence</div>
        <DriveJsonBlock :value="record.terminal_evidence" />
      </section>

      <!-- Progress timeline -->
      <section v-if="progress.length">
        <div class="drive-section-title">Progress</div>
        <ol class="flex flex-col gap-1.5">
          <li v-for="p in progress" :key="p.progress_id || p.created_at" class="flex gap-2">
            <span class="i-carbon-dot-mark text-iolite mt-0.5 shrink-0" />
            <div>
              <div class="text-warm-700 dark:text-warm-300">{{ p.summary }}</div>
              <div class="text-[10px] text-warm-400">{{ actorLabel(p.actor) }} · {{ relativeTime(p.created_at) }}</div>
            </div>
          </li>
        </ol>
      </section>

      <!-- Delivery history -->
      <section>
        <div class="drive-section-title">Deliveries</div>
        <DriveDeliveryList :deliveries="deliveries" :can-replay="has('replay')" :replaying="replaying" @replay="$emit('replay', $event)" />
      </section>

      <!-- Audit summary -->
      <section v-if="audit.length">
        <div class="drive-section-title">Recent audit</div>
        <ul class="flex flex-col gap-1 text-[11px]">
          <li v-for="a in audit" :key="a.audit_id || a.created_at" class="flex items-center gap-2">
            <span class="font-mono text-warm-500">{{ a.operation }}</span>
            <span v-if="a.before_status || a.after_status" class="text-warm-400">{{ a.before_status || "—" }} → {{ a.after_status || "—" }}</span>
            <span class="flex-1" />
            <span class="text-warm-400">{{ actorLabel(a.actor) }} · {{ relativeTime(a.created_at) }}</span>
          </li>
        </ul>
      </section>
    </div>

    <!-- Capability-filtered actions -->
    <div v-if="!readonly" class="shrink-0 border-t border-warm-200 dark:border-warm-700 px-3 py-2 flex flex-col gap-2">
      <!-- Progress input -->
      <div v-if="has('report_progress')" class="flex items-center gap-2">
        <el-input v-model="progressText" size="small" placeholder="Report progress…" @keyup.enter="submitProgress" />
        <el-button size="small" :disabled="!progressText.trim()" @click="submitProgress">Add</el-button>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <el-button v-if="has('update')" size="small" @click="$emit('edit')">Edit</el-button>
        <template v-if="has('transition')">
          <!-- A WAITING Drive re-arms through the dedicated wake op, not a
               transition to ``waiting`` (which just parks it) — R1-38. -->
          <el-button v-if="record.status === 'waiting'" size="small" type="primary" plain @click="$emit('wake')">Wake</el-button>
          <el-button v-if="canGo('active') && record.status !== 'waiting'" size="small" @click="$emit('transition', 'active')">Resume</el-button>
          <el-button v-if="canGo('paused')" size="small" @click="$emit('transition', 'paused')">Pause</el-button>
          <el-button v-if="canGo('blocked')" size="small" @click="$emit('transition', 'blocked')">Block</el-button>
        </template>
        <el-button v-if="has('propose_terminal') && !pendingProposal" size="small" type="primary" plain @click="$emit('propose-terminal')">Propose complete</el-button>
        <!-- Approve/reject only when there is a proposal to verify (its id
             arrives via the propose response or drive_proposal_pending event). -->
        <template v-if="has('verify_terminal') && pendingProposal">
          <el-button size="small" type="success" plain @click="$emit('verify-terminal', true)">Approve</el-button>
          <el-button size="small" type="danger" plain @click="$emit('verify-terminal', false)">Reject</el-button>
        </template>
        <el-button v-if="has('assign') || has('reassign')" size="small" @click="$emit('assign')">Assign…</el-button>
        <el-button v-if="has('unassign')" size="small" @click="$emit('unassign')">Unassign</el-button>
        <el-button v-if="has('transfer_owner')" size="small" @click="$emit('transfer-owner')">Transfer owner…</el-button>
        <el-button v-if="has('transition')" size="small" type="danger" plain @click="$emit('transition', 'cancelled')">Cancel</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import DriveDeliveryList from "@/components/drives/DriveDeliveryList.vue"
import DriveJsonBlock from "@/components/drives/DriveJsonBlock.vue"
import { availabilityDisplay, isAttention, statusDisplay, actorLabel, relativeTime, TONE_TEXT } from "@/utils/driveStatus"

const props = defineProps({
  record: { type: Object, required: true },
  detail: { type: Object, default: null },
  deliveries: { type: Array, default: () => [] },
  flags: { type: Object, default: () => ({}) },
  allowedActions: { type: Array, default: () => [] },
  replaying: { type: String, default: null },
  /** proposal_id when a terminal completion awaits verification, else null. */
  pendingProposal: { type: String, default: null },
  readonly: { type: Boolean, default: false },
  closable: { type: Boolean, default: false },
})

const emit = defineEmits(["transition", "wake", "assign", "unassign", "transfer-owner", "report-progress", "propose-terminal", "verify-terminal", "edit", "replay", "close"])

const showSpec = ref(false)
const progressText = ref("")

watch(
  () => props.record?.drive_id,
  () => {
    showSpec.value = false
    progressText.value = ""
  },
)

const status = computed(() => statusDisplay(props.record.status))
const availability = computed(() => availabilityDisplay(props.record.availability))
const attention = computed(() => isAttention(props.record, props.flags))
const progress = computed(() => props.detail?.progress || [])
const audit = computed(() => props.detail?.audit || [])

function has(action) {
  return props.allowedActions.includes(action)
}

// Only offer a transition target that differs from the current status.
function canGo(target) {
  return props.record.status !== target
}

function hasContent(obj) {
  return obj && typeof obj === "object" && Object.keys(obj).length > 0
}

function submitProgress() {
  const text = progressText.value.trim()
  if (!text) return
  emit("report-progress", text)
  progressText.value = ""
}
</script>

<style scoped>
.drive-section-title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--el-text-color-placeholder, #909399);
  margin-bottom: 0.25rem;
}
</style>
