<template>
  <!-- kt.ui steps (numbered, with done / current / todo) and timeline (dated entries). -->
  <ol class="ktui-steps" :class="`ktui-steps-${mode}`" :data-test="`ktui-${mode}`">
    <li v-for="(entry, i) in entries" :key="i" class="ktui-step" :class="[`ktui-step-${entry.status}`, entry.tone ? `ktui-tone-${entry.tone}` : '']">
      <span class="ktui-step-mark" aria-hidden="true">{{ mark(entry, i) }}</span>
      <div class="ktui-step-body">
        <div v-if="entry.date" class="ktui-step-date">{{ entry.date }}</div>
        <div class="ktui-step-title">{{ entry.title }}</div>
        <div v-if="entry.detail" class="ktui-step-detail">{{ entry.detail }}</div>
      </div>
    </li>
  </ol>
</template>

<script setup>
import { computed } from "vue"

const STATUSES = new Set(["done", "current", "todo"])
const TONES = new Set(["neutral", "info", "success", "warning", "danger"])

const props = defineProps({
  mode: { type: String, default: "steps" },
  items: { type: Array, default: () => [] },
  current: { type: Number, default: null },
  display: { type: Function, default: (v) => String(v ?? "") },
})

const entries = computed(() =>
  props.items.map((item, i) => {
    const raw = item && typeof item === "object" ? item : { title: item }
    let status = STATUSES.has(raw.status) ? raw.status : null
    if (!status && props.mode === "steps" && Number.isFinite(props.current)) {
      status = i < props.current ? "done" : i === props.current ? "current" : "todo"
    }
    return {
      title: props.display(raw.title),
      detail: raw.detail != null ? props.display(raw.detail) : "",
      date: raw.date != null ? props.display(raw.date) : "",
      tone: TONES.has(raw.tone) ? raw.tone : "",
      status: status || "todo",
    }
  }),
)

function mark(entry, i) {
  if (props.mode === "timeline") return ""
  return entry.status === "done" ? "✓" : String(i + 1)
}
</script>
