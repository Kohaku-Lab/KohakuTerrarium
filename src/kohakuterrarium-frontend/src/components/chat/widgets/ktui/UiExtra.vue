<template>
  <!-- kt.ui media, link and widget components: html, math, link, icon, gallery, compare, copy, timer, quiz, rating. -->
  <div v-if="type === 'html'" class="ktui-html" data-test="ktui-html">
    <component :is="appFrame" v-if="htmlSource" :html="htmlSource" :title="node.title ? text(node.title) : t('chat.inlineUi.htmlTitle')" :bridge="true" :fixed-height="fixedHeight" />
    <div v-else class="ktui-note ktui-html-pending" data-test="ktui-html-pending">{{ streaming ? t("chat.inlineUi.drawing") : t("chat.inlineUi.emptyHtml") }}</div>
  </div>
  <div v-else-if="type === 'math'" class="ktui-math" :class="{ 'ktui-math-display': mathDisplay }" data-test="ktui-math" v-html="mathHtml" />
  <span v-else-if="type === 'link'" class="ktui-link-wrap">
    <el-button v-if="node.style === 'button'" size="small" :disabled="!linkOk" data-test="ktui-link" @click="openLink"><span class="i-carbon-launch mr-1" />{{ linkLabel }}</el-button>
    <a v-else class="ktui-link" :class="{ 'ktui-link-off': !linkOk }" :href="linkOk ? href : undefined" rel="noopener noreferrer" data-test="ktui-link" @click.prevent="openLink">{{ linkLabel }}<span class="i-carbon-launch ktui-link-mark" /></a>
  </span>
  <span v-else-if="type === 'icon'" class="ktui-icon" :class="[`ktui-icon-${node.size || 'md'}`, `ktui-tone-${node.tone || 'neutral'}`]" data-test="ktui-icon">
    <span :class="iconClass" :aria-label="node.label ? undefined : node.name" :role="node.label ? undefined : 'img'" />
    <span v-if="node.label">{{ text(node.label) }}</span>
  </span>
  <div v-else-if="type === 'gallery'" class="ktui-gallery" :style="{ gridTemplateColumns: `repeat(${galleryColumns}, minmax(0, 1fr))` }" data-test="ktui-gallery">
    <figure v-for="(img, i) in galleryItems" :key="i" class="ktui-gallery-item">
      <button v-if="img.src" type="button" class="ktui-gallery-open" :title="img.caption || img.alt" @click="zoomed = img"><img :src="img.src" :alt="img.alt" loading="lazy" /></button>
      <div v-else class="ktui-note">{{ img.alt || "image not shown" }}</div>
      <figcaption v-if="img.caption">{{ img.caption }}</figcaption>
    </figure>
    <div v-if="zoomed" class="ktui-gallery-zoom" role="dialog" :aria-label="zoomed.caption || zoomed.alt" data-test="ktui-gallery-zoom" @click="zoomed = null">
      <img :src="zoomed.src" :alt="zoomed.alt" />
      <button type="button" class="ktui-gallery-close" :title="t('chat.inlineUi.closeImage')"><span class="i-carbon-close" /></button>
    </div>
  </div>
  <div v-else-if="type === 'compare'" class="ktui-compare" data-test="ktui-compare">
    <section v-for="(item, i) in compareItems" :key="i" class="ktui-compare-item">
      <div class="ktui-compare-title">{{ item.title }}</div>
      <ul v-if="item.pros.length" class="ktui-compare-list" :aria-label="t('chat.inlineUi.pros')">
        <li v-for="(p, j) in item.pros" :key="j" class="ktui-compare-pro"><span class="i-carbon-checkmark" aria-hidden="true" />{{ p }}</li>
      </ul>
      <ul v-if="item.cons.length" class="ktui-compare-list" :aria-label="t('chat.inlineUi.cons')">
        <li v-for="(c, j) in item.cons" :key="j" class="ktui-compare-con"><span class="i-carbon-close" aria-hidden="true" />{{ c }}</li>
      </ul>
      <div v-if="item.verdict" class="ktui-compare-verdict">{{ item.verdict }}</div>
    </section>
  </div>
  <div v-else-if="type === 'copy'" class="ktui-copy" data-test="ktui-copy">
    <span v-if="node.label" class="ktui-label">{{ text(node.label) }}</span>
    <code class="ktui-copy-value">{{ copyValue }}</code>
    <el-button size="small" data-test="ktui-copy-button" @click="copy"><span :class="copied ? 'i-carbon-checkmark' : 'i-carbon-copy'" class="mr-1" />{{ copied ? t("chat.inlineUi.copied") : t("chat.inlineUi.copy") }}</el-button>
  </div>
  <UiTimer v-else-if="type === 'timer'" :mode="node.mode || 'countdown'" :seconds="num(node.seconds, 0)" :label="node.label ? text(node.label) : ''" :autostart="flagValue(node.autostart)" />
  <div v-else-if="type === 'quiz'" class="ktui-quiz" data-test="ktui-quiz">
    <div class="ktui-quiz-question">{{ text(node.question) }}</div>
    <div class="ktui-quiz-options" role="radiogroup">
      <label v-for="(opt, i) in quizOptions" :key="i" class="ktui-quiz-option" :class="optionClass(opt)">
        <input type="radio" :name="groupName" :value="String(opt.value)" :checked="choice === String(opt.value)" :disabled="revealed" @change="choose(opt)" />
        <span>{{ opt.label }}</span>
      </label>
    </div>
    <div class="ktui-quiz-actions">
      <el-button v-if="!revealed" size="small" type="primary" :disabled="choice === null" data-test="ktui-quiz-check" @click="setUi({ revealed: true })">{{ t("chat.inlineUi.check") }}</el-button>
      <template v-else>
        <span class="ktui-quiz-result" :class="isRight ? 'ktui-tone-success' : 'ktui-tone-danger'" data-test="ktui-quiz-result">{{ isRight ? t("chat.inlineUi.correct") : t("chat.inlineUi.wrong", { answer: answerLabel }) }}</span>
        <el-button size="small" link type="primary" data-test="ktui-quiz-retry" @click="setUi({ revealed: false, choice: null })">{{ t("chat.inlineUi.retry") }}</el-button>
      </template>
    </div>
    <div v-if="revealed && node.explain" class="ktui-quiz-explain">{{ text(node.explain) }}</div>
  </div>
  <label v-else-if="type === 'rating'" class="ktui-field ktui-field-rating">
    <span v-if="node.label" class="ktui-label">{{ text(node.label) }}</span>
    <span class="ktui-rating" role="radiogroup" data-test="ktui-rating">
      <button v-for="n in ratingMax" :key="n" type="button" class="ktui-star" :class="{ 'ktui-star-on': n <= ratingValue }" role="radio" :aria-checked="n === ratingValue" :aria-label="t('chat.inlineUi.rate', { n, max: ratingMax })" :disabled="ctx.disabled" @click="ctx.setVar(node.bind, n)">
        <span :class="n <= ratingValue ? 'i-carbon-star-filled' : 'i-carbon-star'" />
      </button>
    </span>
  </label>
</template>

<script>
// Module scope: one counter for every instance, so radio groups never share a name.
let instances = 0
</script>

<script setup>
import { getActivePinia } from "pinia"
import { computed, ref } from "vue"
import { ElButton, ElMessageBox } from "element-plus"
import katex from "katex"

import { translate, useI18n } from "@/utils/i18n"

import AppFrame from "../app/AppFrame.vue"
import { findWidgetRenderer } from "../../../../ext/widgets.js"
import { useKtUi } from "./context.js"
import { toDisplay, truthy } from "./expr.js"
import { normalizeOptions, resolve, resolveText, safeImageSrc } from "./runtime.js"
import UiTimer from "./UiTimer.vue"

const ICON_CLASSES = {
  check: "i-carbon-checkmark",
  close: "i-carbon-close",
  warning: "i-carbon-warning-alt",
  info: "i-carbon-information",
  error: "i-carbon-error",
  help: "i-carbon-help",
  star: "i-carbon-star",
  heart: "i-carbon-favorite",
  time: "i-carbon-time",
  calendar: "i-carbon-calendar",
  user: "i-carbon-user",
  users: "i-carbon-user-multiple",
  settings: "i-carbon-settings",
  search: "i-carbon-search",
  link: "i-carbon-link",
  download: "i-carbon-download",
  upload: "i-carbon-upload",
  document: "i-carbon-document",
  folder: "i-carbon-folder",
  code: "i-carbon-code",
  chart: "i-carbon-chart-line",
  lightning: "i-carbon-lightning",
  rocket: "i-carbon-rocket",
  idea: "i-carbon-idea",
  lock: "i-carbon-locked",
  flag: "i-carbon-flag",
  location: "i-carbon-location",
  email: "i-carbon-email",
  chat: "i-carbon-chat",
  money: "i-carbon-currency-dollar",
}
const OUTWARD = /^https?:\/\/[^\s<>"']+$/i
const COPIED_MS = 1500

const props = defineProps({
  node: { type: Object, required: true },
  scope: { type: Object, required: true },
  path: { type: String, default: "root" },
})

// Hosts without a Pinia store (package widgets, tests) get the English text.
const t = getActivePinia() ? useI18n().t : (key, args) => translate("en", key, args)
const ctx = useKtUi()
const zoomed = ref(null)
const groupName = `ktui-quiz-${++instances}`
const copied = ref(false)

const type = computed(() => props.node.type)
const ui = computed(() => ctx.uiState?.(props.path) || {})
const streaming = computed(() => ctx.streaming === true)
// The host's ``kt.app`` renderer draws the frame, so a host without app frames shows its stand-in.
const appFrame = computed(() => findWidgetRenderer("kt.app")?.component || AppFrame)
const htmlSource = computed(() => (typeof props.node.source === "string" ? props.node.source : ""))
const fixedHeight = computed(() => num(props.node.height, null))
const mathDisplay = computed(() => flagValue(props.node.display))
const mathHtml = computed(() => katex.renderToString(String(props.node.value ?? ""), { displayMode: mathDisplay.value, throwOnError: false, trust: false, strict: "ignore" }))
const href = computed(() => text(props.node.href))
const linkOk = computed(() => OUTWARD.test(href.value))
const linkLabel = computed(() => (props.node.label ? text(props.node.label) : href.value))
const iconClass = computed(() => ICON_CLASSES[props.node.name] || "i-carbon-help")
const galleryColumns = computed(() => Math.min(6, Math.max(1, Math.round(num(props.node.columns, 3)))))
const galleryItems = computed(() =>
  listOf(props.node.items).map((item) => {
    const raw = item && typeof item === "object" ? item : { src: item }
    return { src: safeImageSrc(valueOf(raw.src)), alt: shown(raw.alt ?? ""), caption: shown(raw.caption ?? "") }
  }),
)
const compareItems = computed(() =>
  listOf(props.node.items).map((item) => ({
    title: shown(item?.title ?? ""),
    pros: Array.isArray(item?.pros) ? item.pros.map(shown) : [],
    cons: Array.isArray(item?.cons) ? item.cons.map(shown) : [],
    verdict: item?.verdict ? shown(item.verdict) : "",
  })),
)
const copyValue = computed(() => text(props.node.value))
const quizOptions = computed(() => normalizeOptions(listOf(props.node.options)))
const choice = computed(() => (typeof ui.value.choice === "string" ? ui.value.choice : null))
const revealed = computed(() => ui.value.revealed === true && choice.value !== null)
const isRight = computed(() => choice.value === String(props.node.answer))
const answerLabel = computed(() => quizOptions.value.find((o) => String(o.value) === String(props.node.answer))?.label ?? String(props.node.answer))
const ratingMax = computed(() => Math.min(10, Math.max(3, Math.round(num(props.node.max, 5)))))
const ratingValue = computed(() => {
  const value = props.scope[props.node.bind]
  return typeof value === "number" ? Math.round(value) : 0
})

function valueOf(value) {
  return resolve(value, props.scope).value
}

function text(value) {
  return resolveText(value, props.scope)
}

function shown(value) {
  return typeof value === "string" ? text(value) : toDisplay(value)
}

function listOf(value) {
  const out = valueOf(value)
  return Array.isArray(out) ? out : []
}

function num(value, fallback) {
  const out = valueOf(value)
  return typeof out === "number" && Number.isFinite(out) ? out : fallback
}

function flagValue(value) {
  if (value === undefined) return false
  const { value: out, error } = resolve(value, props.scope)
  return !error && truthy(out)
}

function setUi(patch) {
  ctx.setUiState?.(props.path, patch)
}

function choose(opt) {
  setUi({ choice: String(opt.value), revealed: false })
}

function optionClass(opt) {
  if (!revealed.value) return {}
  const value = String(opt.value)
  return { "ktui-quiz-right": value === String(props.node.answer), "ktui-quiz-wrong": value === choice.value && !isRight.value }
}

async function openLink() {
  if (!linkOk.value) return
  try {
    await ElMessageBox.confirm(href.value, t("chat.inlineUi.openLink"), { confirmButtonText: t("chat.inlineUi.open"), cancelButtonText: t("chat.inlineUi.cancel") })
    globalThis.open?.(href.value, "_blank", "noopener,noreferrer")
  } catch {
    /* dismissed */
  }
}

async function copy() {
  const value = copyValue.value
  try {
    await globalThis.navigator.clipboard.writeText(value)
  } catch {
    const area = document.createElement("textarea")
    area.value = value
    document.body.appendChild(area)
    area.select()
    document.execCommand?.("copy")
    area.remove()
  }
  copied.value = true
  setTimeout(() => (copied.value = false), COPIED_MS)
}
</script>
