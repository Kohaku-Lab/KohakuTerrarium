<template>
  <el-dialog :model-value="modelValue" :title="dialogTitle" width="min(560px, 92vw)" @update:model-value="$emit('update:modelValue', $event)" @closed="onClosed">
    <!-- Stale-edit conflict banner (revision-aware compare) -->
    <div v-if="conflict" class="mb-3 rounded border-l-3 border-l-amber bg-amber/10 px-3 py-2 text-[12px]">
      <div class="flex items-center gap-2 text-amber-shadow dark:text-amber-light font-medium"><span class="i-carbon-warning" /> This Drive changed since you opened it.</div>
      <p class="text-warm-500 mt-1">
        Server revision is now <span class="font-mono">{{ conflict.server?.revision }}</span> (you edited from <span class="font-mono">{{ baseRevision }}</span
        >). Load the server copy to compare, then re-apply your changes.
      </p>
      <el-button size="small" class="mt-1" @click="loadServer">Load server copy</el-button>
    </div>

    <div class="flex flex-col gap-3">
      <div v-if="mode === 'create'" class="flex flex-col gap-1">
        <label class="text-[11px] text-warm-500">Kind</label>
        <el-select v-model="form.kind" size="small" placeholder="Select a kind" class="w-full">
          <el-option v-for="k in kinds" :key="k" :value="k" :label="k" />
        </el-select>
        <p v-if="!kinds.length" class="text-[11px] text-amber-shadow dark:text-amber-light">No enabled Drive registrations on this node — enable one in Settings → Drives first.</p>
      </div>

      <!-- Goal kind: a form over the goal spec instead of raw JSON. -->
      <template v-if="isGoal">
        <div class="flex flex-col gap-1">
          <label class="text-[11px] text-warm-500">Objective</label>
          <el-input v-model="goal.objective" type="textarea" :rows="2" size="small" placeholder="What should the creature keep pursuing?" data-testid="goal-objective" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="flex flex-col gap-1">
            <label class="text-[11px] text-warm-500">Autonomy</label>
            <el-select v-model="goal.autonomy" size="small" class="w-full" data-testid="goal-autonomy">
              <el-option value="manual" label="manual — one turn per resume" />
              <el-option value="continue_when_ready" label="continue_when_ready — keeps going" />
            </el-select>
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-[11px] text-warm-500">Completion</label>
            <el-select v-model="goal.completion_policy" size="small" class="w-full" data-testid="goal-policy">
              <el-option value="self_propose" label="self_propose" />
              <el-option value="user_confirm" label="user_confirm" />
              <el-option value="verifier" label="verifier" />
            </el-select>
          </div>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-[11px] text-warm-500">Success criteria (one per line)</label>
          <el-input v-model="goal.criteriaText" type="textarea" :rows="2" size="small" data-testid="goal-criteria" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-[11px] text-warm-500">Constraints (one per line)</label>
          <el-input v-model="goal.constraintsText" type="textarea" :rows="2" size="small" data-testid="goal-constraints" />
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div class="flex flex-col gap-1">
            <label class="text-[11px] text-warm-500">Max turns</label>
            <el-input-number v-model="goal.max_turns" size="small" :min="1" controls-position="right" class="!w-full" data-testid="goal-max-turns" />
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-[11px] text-warm-500">Max tool calls</label>
            <el-input-number v-model="goal.max_tool_calls" size="small" :min="1" controls-position="right" class="!w-full" />
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-[11px] text-warm-500">Max walltime (s)</label>
            <el-input-number v-model="goal.max_walltime_s" size="small" :min="1" controls-position="right" class="!w-full" />
          </div>
        </div>
        <p class="text-[11px] text-warm-500">A budget pauses the goal when spent; it never completes it. Leave a budget empty for no limit.</p>
      </template>

      <div class="flex flex-col gap-1">
        <label class="text-[11px] text-warm-500">Title</label>
        <el-input v-model="form.title" size="small" :placeholder="isGoal ? 'Defaults to the objective' : 'What is this Drive for?'" />
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-[11px] text-warm-500">Priority</label>
          <el-input-number v-model="form.priority" size="small" controls-position="right" class="!w-full" />
        </div>
        <div v-if="mode === 'create'" class="flex flex-col gap-1">
          <label class="text-[11px] text-warm-500">Scope</label>
          <el-select v-model="form.scope_type" size="small" class="w-full">
            <el-option value="graph" label="graph" />
            <el-option value="creature" label="creature" />
          </el-select>
        </div>
      </div>

      <!-- Creature picker — required for a creature-scoped Drive so scope_id is
           a real member, not the graph id (R1-38). A graph-scoped goal still
           needs an assignee to pursue it. -->
      <div v-if="mode === 'create' && (form.scope_type === 'creature' || isGoal)" class="flex flex-col gap-1">
        <label class="text-[11px] text-warm-500">{{ form.scope_type === "creature" ? "Creature" : "Assign to" }}</label>
        <el-select v-model="form.scope_id" size="small" placeholder="Select a creature" class="w-full" data-testid="creature-picker">
          <el-option v-for="c in creatures" :key="c.creature_id" :value="c.creature_id" :label="c.name || c.creature_id" />
        </el-select>
        <p v-if="!creatures.length" class="text-[11px] text-amber-shadow dark:text-amber-light">No creatures in this session to scope a Drive to.</p>
      </div>

      <DriveJsonField v-model="form.presentationText" label="Presentation (JSON)" collapsed-by-default @error="presentationErr = $event" />
      <DriveJsonField v-if="!isGoal" v-model="form.specText" label="Spec (JSON)" @error="specErr = $event" />
      <DriveJsonField v-model="form.metadataText" label="Metadata (JSON)" collapsed-by-default @error="metadataErr = $event" />
    </div>

    <template #footer>
      <div class="flex items-center gap-2">
        <span v-if="anyError" class="text-[11px] text-coral flex-1 text-left">{{ anyError }}</span>
        <el-button size="small" @click="$emit('update:modelValue', false)">Cancel</el-button>
        <el-button size="small" type="primary" :loading="saving" :disabled="!canSubmit" @click="submit">
          {{ mode === "create" ? "Create" : "Save" }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"

import DriveJsonField from "@/components/drives/DriveJsonField.vue"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  mode: { type: String, default: "create" },
  record: { type: Object, default: null },
  kinds: { type: Array, default: () => [] },
  /** Session members, for the creature-scope picker: [{creature_id, name}]. */
  creatures: { type: Array, default: () => [] },
  conflict: { type: Object, default: null },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "create", "save", "load-server"])

const form = reactive({
  kind: "",
  title: "",
  priority: 0,
  scope_type: "graph",
  scope_id: "",
  presentationText: "{}",
  specText: "{}",
  metadataText: "{}",
})

// Goal-kind fields; folded into ``spec`` on submit.
const goal = reactive({
  objective: "",
  criteriaText: "",
  constraintsText: "",
  autonomy: "manual",
  completion_policy: "self_propose",
  max_turns: null,
  max_tool_calls: null,
  max_walltime_s: null,
})

const specErr = ref("")
const presentationErr = ref("")
const metadataErr = ref("")
const baseRevision = ref(null)

const isGoal = computed(() => (props.mode === "create" ? form.kind : props.record?.kind) === "goal")
const dialogTitle = computed(() => {
  if (isGoal.value) return props.mode === "create" ? "Create Goal" : "Edit Goal"
  return props.mode === "create" ? "Create Drive" : "Edit Drive"
})

const anyError = computed(() => specErr.value || presentationErr.value || metadataErr.value || "")
const canSubmit = computed(() => {
  if (anyError.value) return false
  if (isGoal.value) {
    if (!goal.objective.trim()) return false
  } else if (!form.title.trim()) {
    return false
  }
  if (props.mode === "create" && !form.kind) return false
  // A creature-scoped Drive must name a real creature (R1-38); a goal always
  // needs someone to pursue it.
  if (props.mode === "create" && (form.scope_type === "creature" || isGoal.value) && !form.scope_id) return false
  return true
})

watch(
  () => [props.modelValue, props.record?.drive_id],
  () => {
    if (props.modelValue) resetForm(props.record)
  },
  { immediate: true },
)

function resetForm(record) {
  if (record) {
    form.kind = record.kind || ""
    form.title = record.title || ""
    form.priority = record.priority || 0
    form.scope_type = record.scope_type || "graph"
    form.scope_id = record.scope_id || ""
    form.presentationText = pretty(record.presentation)
    form.specText = pretty(record.spec)
    form.metadataText = pretty(record.metadata)
    baseRevision.value = record.revision
    loadGoal(record.kind === "goal" ? record.spec : null)
  } else {
    form.kind = props.kinds[0] || ""
    form.title = ""
    form.priority = 0
    form.scope_type = "graph"
    form.scope_id = ""
    form.presentationText = "{}"
    form.specText = "{}"
    form.metadataText = "{}"
    baseRevision.value = null
    loadGoal(null)
  }
  specErr.value = presentationErr.value = metadataErr.value = ""
}

function loadGoal(spec) {
  const s = spec && typeof spec === "object" ? spec : {}
  const budgets = s.budgets && typeof s.budgets === "object" ? s.budgets : {}
  goal.objective = s.objective || ""
  goal.criteriaText = (s.success_criteria || []).join("\n")
  goal.constraintsText = (s.constraints || []).join("\n")
  goal.autonomy = s.autonomy || "manual"
  goal.completion_policy = s.completion_policy || "self_propose"
  goal.max_turns = budgets.max_turns ?? null
  goal.max_tool_calls = budgets.max_tool_calls ?? null
  goal.max_walltime_s = budgets.max_walltime_s ?? null
}

function goalSpec() {
  const lines = (text) =>
    text
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
  const budget = (v) => (Number.isInteger(v) && v > 0 ? v : null)
  return {
    objective: goal.objective.trim(),
    success_criteria: lines(goal.criteriaText),
    constraints: lines(goal.constraintsText),
    autonomy: goal.autonomy,
    completion_policy: goal.completion_policy,
    budgets: {
      max_turns: budget(goal.max_turns),
      max_tool_calls: budget(goal.max_tool_calls),
      max_walltime_s: budget(goal.max_walltime_s),
    },
  }
}

function loadServer() {
  if (props.conflict?.server) {
    resetForm(props.conflict.server)
    emit("load-server")
  }
}

function onClosed() {
  emit("update:modelValue", false)
}

function submit() {
  if (!canSubmit.value) return
  const presentation = parse(form.presentationText)
  const spec = isGoal.value ? goalSpec() : parse(form.specText)
  const metadata = parse(form.metadataText)
  const title = form.title.trim() || (isGoal.value ? goal.objective.trim().slice(0, 120) : "")
  if (props.mode === "create") {
    const payload = {
      kind: form.kind,
      title,
      priority: form.priority,
      scope_type: form.scope_type,
      presentation,
      spec,
      metadata,
    }
    // Only a creature scope carries an explicit scope_id (the chosen member);
    // a graph-scoped Drive lets the panel default it to the graph (R1-38).
    if (form.scope_type === "creature") payload.scope_id = form.scope_id
    // A graph-scoped goal still names the creature that pursues it.
    if (isGoal.value && form.scope_id) payload.assignee_creature_id = form.scope_id
    emit("create", payload)
  } else {
    emit("save", {
      patch: { title, priority: form.priority, presentation, spec, metadata },
      expectedRevision: baseRevision.value,
    })
  }
}

function pretty(obj) {
  try {
    return JSON.stringify(obj || {}, null, 2)
  } catch {
    return "{}"
  }
}
function parse(text) {
  try {
    return text.trim() === "" ? {} : JSON.parse(text)
  } catch {
    return {}
  }
}
</script>
