<template>
  <!-- kt.ui table: column formats, and local sort / filter when enabled. -->
  <div class="ktui-table-wrap" data-test="ktui-table">
    <input v-if="filter" v-model="query" class="ktui-table-filter" type="search" :placeholder="filterLabel" :aria-label="filterLabel" data-test="ktui-table-filter" />
    <table class="ktui-table">
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.index" :style="{ textAlign: col.align }" :aria-sort="ariaSort(col)">
            <button v-if="sortable" type="button" class="ktui-sort" data-test="ktui-sort" @click="toggleSort(col)">
              {{ label(col) }}<span class="ktui-sort-mark">{{ sortMark(col) }}</span>
            </button>
            <template v-else>{{ label(col) }}</template>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, r) in shownRows" :key="r">
          <td v-for="col in columns" :key="col.index" :style="{ textAlign: col.align }">{{ cell(row, col) }}</td>
        </tr>
        <tr v-if="!shownRows.length && rows.length">
          <td :colspan="columns.length || 1" class="ktui-note">{{ noMatch }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { getActivePinia } from "pinia"
import { computed, ref, watch } from "vue"

import { translate, useI18n } from "@/utils/i18n"

import { filterRows, formatCell, sortRows, tableCell } from "./runtime.js"

const props = defineProps({
  columns: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  sortable: { type: Boolean, default: false },
  filter: { type: Boolean, default: false },
  display: { type: Function, default: (v) => String(v ?? "") },
  // Saved sort and filter ``{q, sort, dir}``; changes are emitted as ``ui``.
  uiState: { type: Object, default: () => ({}) },
})

const emit = defineEmits(["ui"])

// Hosts without a Pinia store (package widgets, tests) get the English text.
const t = getActivePinia() ? useI18n().t : (key) => translate("en", key)
const filterLabel = computed(() => t("chat.inlineUi.filterRows"))
const noMatch = computed(() => t("chat.inlineUi.noRowsMatch"))

const query = ref("")
const sortIndex = ref(-1)
const sortDir = ref("asc")

function adopt(saved) {
  if (typeof saved?.q === "string") query.value = saved.q
  if (Number.isInteger(saved?.sort)) sortIndex.value = saved.sort
  if (saved?.dir === "asc" || saved?.dir === "desc") sortDir.value = saved.dir
}

adopt(props.uiState)
watch(() => props.uiState, adopt)
watch(query, (q) => {
  if (q !== (props.uiState?.q ?? "")) emit("ui", { q })
})

const shownRows = computed(() => {
  const filtered = filterRows(props.rows, props.columns, query.value)
  const column = props.columns.find((c) => c.index === sortIndex.value)
  return column ? sortRows(filtered, column, sortDir.value) : filtered
})

function label(col) {
  return props.display(col.label)
}

function cell(row, col) {
  const value = tableCell(row, col)
  return col.format && col.format !== "text" ? formatCell(value, col) : props.display(value)
}

function toggleSort(col) {
  if (sortIndex.value !== col.index) {
    sortIndex.value = col.index
    sortDir.value = "asc"
  } else if (sortDir.value === "asc") sortDir.value = "desc"
  else sortIndex.value = -1
  emit("ui", { sort: sortIndex.value, dir: sortDir.value })
}

function sortMark(col) {
  if (sortIndex.value !== col.index) return ""
  return sortDir.value === "asc" ? " ▲" : " ▼"
}

function ariaSort(col) {
  if (!props.sortable) return undefined
  if (sortIndex.value !== col.index) return "none"
  return sortDir.value === "asc" ? "ascending" : "descending"
}
</script>
