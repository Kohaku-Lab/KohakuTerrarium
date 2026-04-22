<template>
  <div class="rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral-shadow dark:text-coral">
    <div class="flex items-center gap-1.5 font-medium mb-1">
      <div class="i-carbon-warning text-sm" />
      {{ t("studio.creature.validation.title", { count: errors.length }) }}
    </div>
    <ul class="flex flex-col gap-0.5 pl-5 list-disc">
      <li v-for="(e, i) in errors" :key="i" class="text-[11px]">
        <span v-if="e.field" class="font-mono opacity-80">{{ e.field }}:</span>
        {{ e.message }}
      </li>
    </ul>
  </div>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  errors: { type: Array, required: true },
})
</script>
