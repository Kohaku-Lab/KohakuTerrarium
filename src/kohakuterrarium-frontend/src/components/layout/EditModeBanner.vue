<template>
  <div
    v-if="layout.editMode"
    class="edit-banner flex items-center gap-3 px-4 h-9 text-xs bg-amber/15 border-b border-amber/40 text-amber-shadow dark:text-amber-light"
  >
    <div class="i-carbon-settings-edit text-base" />
    <span class="font-medium">Layout edit mode</span>
    <span class="text-warm-500">·</span>
    <span class="text-warm-600 dark:text-warm-400">{{
      activePresetLabel
    }}</span>
    <span
      v-if="layout.editModeDirty"
      class="text-amber font-semibold text-[10px] uppercase"
      >● unsaved</span
    >
    <div class="flex-1" />
    <button
      class="px-2 py-0.5 rounded bg-amber/20 hover:bg-amber/30 text-amber-shadow transition-colors"
      :title="
        canSave
          ? 'Save changes'
          : 'Builtin preset — will open Save As'
      "
      @click="onSave"
    >
      {{ canSave ? 'Save' : 'Save as...' }}
    </button>
    <button
      class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 hover:bg-warm-200 dark:hover:bg-warm-700 text-warm-700 dark:text-warm-300 transition-colors"
      @click="onSaveAs"
    >
      Save as new
    </button>
    <button
      class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 hover:bg-warm-200 dark:hover:bg-warm-700 text-warm-700 dark:text-warm-300 transition-colors"
      :disabled="!layout.editModeDirty"
      @click="onRevert"
    >
      Revert
    </button>
    <button
      class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 hover:bg-warm-200 dark:hover:bg-warm-700 text-warm-700 dark:text-warm-300 transition-colors"
      title="Exit edit mode (Esc)"
      @click="onExit"
    >
      Exit
    </button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from "vue";

import { useLayoutStore } from "@/stores/layout";
import {
  LAYOUT_EVENTS,
  fireLayoutSaveAsRequested,
  onLayoutEvent,
} from "@/utils/layoutEvents";

const layout = useLayoutStore();

const activePresetLabel = computed(
  () => layout.activePreset?.label || layout.activePreset?.id || "—",
);

// Builtin presets can't be saved over (they reset on reload). Force
// users to Save as new in that case.
const canSave = computed(
  () => !!layout.activePreset && !layout.activePreset.builtin,
);

function onSave() {
  if (layout.activePreset?.builtin) {
    // Can't overwrite builtins — redirect to Save As
    fireLayoutSaveAsRequested();
    return;
  }
  layout.saveEditMode();
  layout.exitEditMode();
}

function onSaveAs() {
  fireLayoutSaveAsRequested();
}

function onRevert() {
  if (!layout.editModeDirty) return;
  layout.revertEditMode();
}

function onExit() {
  if (layout.editModeDirty) {
    if (!confirm("Discard unsaved layout changes?")) return;
    layout.revertEditMode();
  }
  layout.exitEditMode();
}

function onKey(e) {
  if (!layout.editMode) return;
  if (e.key === "Escape") {
    e.preventDefault();
    onExit();
  }
}

let unsubEdit = () => {};
onMounted(() => {
  window.addEventListener("keydown", onKey);
  unsubEdit = onLayoutEvent(LAYOUT_EVENTS.EDIT_REQUESTED, () => {
    if (layout.editMode) {
      onExit();
    } else {
      layout.enterEditMode();
    }
  });
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  unsubEdit();
});
</script>
