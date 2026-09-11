// Shared media leaf for the two chat hosts. Both consume the injected
// ``ktMediaResolver`` (see ``mediaResolver.js``); neither imports a host route
// or transport. The browser resolver answers with a direct same-origin URL, so
// the Dashboard keeps its native inline rendering. The VS Code resolver answers
// asynchronously with a Host-spooled webview URI.
//
// Video previews are NOT forked here: both hosts render the single production
// ``VideoFilePreview.vue`` (exported from ``public/chat/index.js``) so the
// playable/downloadable markup lives in exactly one place.
import { defineComponent, h } from "vue"

import { useI18n } from "@/utils/i18n"

import { useMediaResource } from "./mediaResolver.js"

function status(value, extraClass = "") {
  return h("span", { class: `kt-media-status ${extraClass}`.trim(), role: "status" }, value)
}

export const MediaImage = defineComponent({
  name: "SharedMediaImage",
  props: {
    src: { type: String, default: "" },
    alt: { type: String, default: "" },
    name: { type: String, default: "" },
    // Optional per-instance overrides; the default resolves through the shared
    // ``chat.media.*`` dictionary so BOTH hosts show genuine localized status.
    labels: { type: Object, default: () => ({}) },
  },
  setup(props) {
    const { t } = useI18n()
    const media = useMediaResource(() => props.src, { kind: "image", name: () => props.name })
    return () => {
      const loading = props.labels?.loading || t("chat.media.loading")
      const unavailable = props.labels?.unavailable || t("chat.media.unavailable")
      if (media.state.value === "error") return status(media.error.value || unavailable, "is-error")
      if (!media.url.value) return media.state.value === "loading" ? status(loading) : null
      return h("img", { class: "kt-conversation-image", src: media.url.value, alt: props.alt })
    }
  },
})
