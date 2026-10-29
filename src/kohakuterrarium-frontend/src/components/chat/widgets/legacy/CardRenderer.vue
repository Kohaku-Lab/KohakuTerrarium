<template>
  <div class="ui-event-card" :class="['accent-' + (spec.accent || 'neutral'), { 'ui-event-done': isResolved && hasActions }]">
    <div class="ui-event-header">
      <span v-if="spec.icon" class="text-sm">{{ spec.icon }}</span>
      <span class="text-sm font-semibold flex-1">{{ spec.title || "" }}</span>
      <span v-if="spec.subtitle" class="text-xs text-warm-500">{{ spec.subtitle }}</span>
    </div>
    <div v-if="spec.body" class="text-sm py-2 px-1">
      <MarkdownRenderer :content="spec.body" :breaks="true" :origin="markdownOrigin" />
    </div>
    <div v-if="(spec.fields || []).length" class="grid gap-x-3 gap-y-1 my-2 px-1" :style="{ gridTemplateColumns: anyInlineField ? '1fr 1fr' : '1fr' }">
      <div v-for="(f, i) in spec.fields" :key="i" class="text-xs" :style="!f.inline ? { gridColumn: '1 / -1' } : {}">
        <span class="font-semibold text-warm-500">{{ f.label }}:</span> {{ f.value }}
      </div>
    </div>
    <div v-if="spec.footer" class="text-[10px] text-warm-400 mt-2 px-1 italic">{{ spec.footer }}</div>
    <div v-if="hasActions && !isResolved" class="flex gap-2 flex-wrap mt-2 items-center">
      <template v-for="entry in resolvedActions" :key="entry.action.id">
        <el-link v-if="entry.kind === 'link'" :href="entry.href" target="_blank" rel="noopener" type="primary" underline="hover" class="card-link">
          <span class="i-carbon-launch text-xs mr-1" />
          {{ entry.action.label || entry.action.id }}
        </el-link>
        <!-- A host that owns a backend URL installs a platform link opener:
             a relative/external card link is resolved Host-side and opened
             only on this user click, never by navigating the webview. -->
        <el-link v-else-if="entry.kind === 'host'" :href="entry.href" target="_blank" rel="noopener" type="primary" underline="hover" class="card-link" @click="onOpenHostLink($event, entry.action.url)">
          <span class="i-carbon-launch text-xs mr-1" />
          {{ entry.action.label || entry.action.id }}
        </el-link>
        <span v-else-if="entry.kind === 'unavailable'" class="card-link card-link-unavailable" :title="entry.action.url" role="status">
          <span class="i-carbon-warning-alt text-xs mr-1" />
          {{ entry.action.label || entry.action.id }} ({{ t("chat.link.unavailable") }})
        </span>
        <el-button v-else-if="entry.kind === 'button'" :type="confirmButtonType(entry.action.style)" @click="host.send(entry.action.id, {})">
          {{ entry.action.label || entry.action.id }}
        </el-button>
      </template>
    </div>
    <div v-else-if="hasActions && repliedActionId" class="text-sm text-warm-500 italic px-1 mt-2">→ {{ repliedActionId }}</div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { ElButton, ElLink } from "element-plus"

import { confirmButtonType, useResolution } from "./legacyShared.js"
import { resolvePlatformLink, shouldOpenThroughHost } from "../../../../public/chat/externalLinks.js"
import MarkdownRenderer from "../../../../public/chat/MarkdownRenderer.vue"
import { usePlatformLinkOpener } from "../../../../public/chat/platformLink.js"
import { usePlatformOrigin } from "../../../../public/chat/platformOrigin.js"
import { useI18n } from "../../../../utils/i18n"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const { t } = useI18n()
const spec = computed(() => props.widget.spec || {})
const { isResolved, repliedActionId } = useResolution(props)

const platformOrigin = usePlatformOrigin()
const platformLinkOpener = usePlatformLinkOpener()
// A host that installed an opener owns relative/external card links; without one
// the browser keeps native navigation (and a genuinely unknown origin shows the
// visible unavailable affordance).
const hostOpensLinks = typeof platformLinkOpener === "function"
const markdownOrigin = computed(() => {
  if (props.host.origin) return props.host.origin
  if (platformOrigin !== undefined) return platformOrigin
  return typeof window !== "undefined" ? window.location.origin : null
})

// Resolve every card action through the one shared link policy. A link action
// the installed platform opener must handle (a relative reference or an
// absolute http(s) URL) is routed to the opener on click so the raw target is
// resolved Host-side; otherwise a safely resolvable target is an anchor, a
// relative target with a genuinely unknown origin renders a VISIBLE unavailable
// affordance rather than being silently dropped, and a `javascript:`/`data:`
// target is dropped entirely. Non-link actions stay buttons.
const resolvedActions = computed(() =>
  (spec.value.actions || []).map((action) => {
    if (action.style !== "link") return { action, kind: "button" }
    const resolved = resolvePlatformLink(action.url, markdownOrigin.value)
    if (hostOpensLinks && shouldOpenThroughHost(action.url)) {
      return { action, kind: "host", href: resolved.href || String(action.url ?? "") }
    }
    if (resolved.href) return { action, kind: "link", href: resolved.href }
    return { action, kind: resolved.unavailable ? "unavailable" : "drop" }
  }),
)

const hasActions = computed(() => (spec.value.actions || []).length > 0)
const anyInlineField = computed(() => (spec.value.fields || []).some((f) => f.inline))

// A host-owned card link: suppress the webview's own navigation and hand the
// raw model-authored target to the installed opener, which resolves it against
// the live backend URL. The opener surfaces a localized failure itself.
function onOpenHostLink(event, target) {
  event?.preventDefault?.()
  if (typeof platformLinkOpener === "function") platformLinkOpener(target)
}
</script>
