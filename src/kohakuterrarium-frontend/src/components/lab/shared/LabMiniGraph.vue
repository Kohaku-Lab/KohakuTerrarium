<template>
  <svg class="kt-lab-mini block w-full h-full" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="xMidYMid meet" aria-hidden="true" data-test="lab-mini-graph">
    <path v-for="l in laid.links" :key="l.id" :d="l.d" class="kt-lab-mini-link" :class="l.control ? 'kt-lab-mini-link--control' : ''" />
    <path v-for="w in laid.wires" :key="w.id" :d="w.d" class="kt-lab-mini-wire" />
    <template v-if="active">
      <circle v-for="(l, i) in flowing" :key="`m-${l.id}`" r="1.8" class="kt-lab-mini-msg">
        <animateMotion :dur="`${1.8 + (i % 3) * 0.5}s`" repeatCount="indefinite" :path="l.d" />
      </circle>
    </template>
    <g v-for="c in laid.channels" :key="c.id">
      <rect :x="c.x" :y="c.y" :width="c.w" :height="c.h" :rx="c.h / 2" class="kt-lab-mini-channel" />
      <text v-if="c.label" :x="c.x + c.w / 2" :y="c.y + c.h / 2 + 3" text-anchor="middle" class="kt-lab-mini-channel-label fill-aquamarine-shadow dark:fill-aquamarine-light">{{ c.label }}</text>
    </g>
    <g v-for="g in laid.groups" :key="g.id" :data-test="`lab-mini-${g.id}`">
      <title>{{ g.dots.map((d) => d.name).join(", ") }}{{ g.more ? ` +${g.more}` : "" }}</title>
      <rect :x="g.x" :y="g.y" :width="g.w" :height="g.h" rx="3" class="kt-lab-mini-node" :class="g.privileged ? 'kt-lab-mini-node--privileged' : 'stroke-warm-300 dark:stroke-warm-600'" />
      <text :x="g.x + 6" :y="g.y + 11" class="kt-lab-mini-count fill-warm-600 dark:fill-warm-300">×{{ g.count }}</text>
      <circle v-for="d in g.dots" :key="d.id" :cx="d.x" :cy="d.y" r="2.5" :class="`kt-lab-mini-bar--${d.status}`" />
      <text v-if="g.more" :x="g.x + g.w - 5" :y="g.y + 11" text-anchor="end" class="kt-lab-mini-count fill-warm-500 dark:fill-warm-400">+{{ g.more }}</text>
    </g>
    <g v-for="n in laid.nodes" :key="n.id" :data-test="`lab-mini-node-${n.id}`">
      <title>{{ n.name }}</title>
      <rect :x="n.x" :y="n.y" :width="n.w" :height="n.h" rx="3" class="kt-lab-mini-node" :class="n.privileged ? 'kt-lab-mini-node--privileged' : 'stroke-warm-300 dark:stroke-warm-600'" />
      <rect :x="n.x" :y="n.y" width="2.5" :height="n.h" rx="1" :class="`kt-lab-mini-bar--${n.status}`" />
      <text v-if="n.label" :x="n.x + 6" :y="n.y + n.h / 2 + 3.4" class="kt-lab-mini-label fill-warm-800 dark:fill-warm-100">{{ n.label }}</text>
    </g>
  </svg>
</template>

<script setup>
import { computed } from "vue"

import { miniGraphLayout } from "@/components/lab/model/miniGraph"

/**
 * One session's graph as a still thumbnail in the Flow view's idiom: stage
 * nodes with a status bar (privileged ones edged in iolite), channel pills,
 * creature–channel links (privileged links in iolite) and output wires; a
 * big session shows folded groups of status dots instead (miniGraphLayout).
 * `active` moves message dots along the links that send.
 */
const props = defineProps({
  session: { type: Object, required: true },
  width: { type: Number, default: 300 },
  height: { type: Number, default: 132 },
  active: { type: Boolean, default: false },
})

const MAX_FLOWING = 6
const laid = computed(() => miniGraphLayout(props.session, props.width, props.height))
const flowing = computed(() => laid.value.links.filter((l) => l.sends && !l.control).slice(0, MAX_FLOWING))
</script>

<style scoped>
.kt-lab-mini-link {
  fill: none;
  stroke: var(--kt-color-aquamarine);
  stroke-opacity: 0.5;
  stroke-width: 1;
}
.kt-lab-mini-link--control {
  stroke: var(--kt-color-iolite);
  stroke-opacity: 0.3;
}
.kt-lab-mini-wire {
  fill: none;
  stroke: var(--kt-color-sapphire);
  stroke-opacity: 0.55;
  stroke-width: 1;
}
.kt-lab-mini-msg {
  fill: var(--kt-color-aquamarine);
}
.kt-lab-mini-channel {
  fill: rgb(76 153 137 / 0.1);
  stroke: rgb(76 153 137 / 0.55);
  stroke-width: 1;
}
.kt-lab-mini-channel-label {
  font-family: ui-monospace, "JetBrains Mono", Consolas, monospace;
  font-size: 9px;
  font-weight: 600;
}
.kt-lab-mini-node {
  fill: var(--v2-card, #fff);
  stroke-width: 1;
}
.kt-lab-mini-node--privileged {
  stroke: rgb(90 79 207 / 0.55);
}
.kt-lab-mini-label {
  font-size: 9.5px;
  font-weight: 600;
}
.kt-lab-mini-count {
  font-family: ui-monospace, "JetBrains Mono", Consolas, monospace;
  font-size: 9px;
}
.kt-lab-mini-bar--busy {
  fill: var(--kt-color-aquamarine);
}
.kt-lab-mini-bar--idle,
.kt-lab-mini-bar--paused {
  fill: var(--kt-color-amber);
}
.kt-lab-mini-bar--paused {
  opacity: 0.55;
}
.kt-lab-mini-bar--stopped {
  fill: var(--kt-color-warm-400);
}
.kt-lab-mini-bar--error {
  fill: var(--kt-color-coral);
}
@media (prefers-reduced-motion: reduce) {
  .kt-lab-mini-msg {
    display: none;
  }
}
</style>
