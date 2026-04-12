<template>
  <div class="h-screen flex overflow-hidden bg-warm-50 dark:bg-warm-950">
    <NavRail />
    <main class="flex-1 overflow-hidden">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
    <CommandPalette />
    <ToastCenter />
  </div>
</template>

<script setup>
import CommandPalette from "@/components/chrome/CommandPalette.vue"
import ToastCenter from "@/components/chrome/ToastCenter.vue"
import NavRail from "@/components/layout/NavRail.vue"
import { useArtifactDetector } from "@/composables/useArtifactDetector"
import { useAutoTriggers } from "@/composables/useAutoTriggers"
import { useBuiltinCommands } from "@/composables/useBuiltinCommands"
import { useKeyboardShortcuts } from "@/composables/useKeyboardShortcuts"
import { useInstancesStore } from "@/stores/instances"
import { useThemeStore } from "@/stores/theme"

const theme = useThemeStore()
theme.init()

const instances = useInstancesStore()
instances.fetchAll()

// Global Ctrl+1..6 preset switcher, Ctrl+Shift+L edit mode, Ctrl+K palette.
useKeyboardShortcuts()
// Register every built-in palette command.
useBuiltinCommands()
// Auto-trigger rules: canvas first-artifact notification, processing
// error → focus debug preset.
useAutoTriggers()
// Scan assistant messages for canvas artifacts regardless of active preset.
useArtifactDetector()
</script>
