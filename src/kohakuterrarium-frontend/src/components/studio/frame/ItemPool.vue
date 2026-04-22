<template>
  <div class="h-full flex flex-col">
    <!-- Optional pool header with title + search -->
    <div v-if="title || searchable" class="shrink-0 px-3 py-2 border-b border-warm-200 dark:border-warm-800">
      <div v-if="title" class="text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400 mb-1.5">
        {{ title }}
      </div>
      <div v-if="searchable" class="relative">
        <div class="i-carbon-search absolute left-2 top-1/2 -translate-y-1/2 text-warm-400 text-xs" />
        <input :value="searchValue" type="text" :placeholder="searchPlaceholder" class="w-full pl-7 pr-2 py-1 text-xs rounded bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 text-warm-800 dark:text-warm-200 placeholder-warm-400 focus:outline-none focus:border-iolite" @input="$emit('update:searchValue', $event.target.value)" />
      </div>
    </div>
    <!-- Scrollable content -->
    <div class="flex-1 min-h-0 overflow-y-auto">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: "" },
  searchable: { type: Boolean, default: false },
  searchValue: { type: String, default: "" },
  searchPlaceholder: { type: String, default: "Search…" },
})

defineEmits(["update:searchValue"])
</script>
