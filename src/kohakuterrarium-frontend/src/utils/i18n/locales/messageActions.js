// Shared message-action strings for the one production message row
// (``MessageRow.vue``) that both hosts render. Merged into each locale by
// ``./index.js`` so a copy failure is surfaced in the reader's language rather
// than as an English-only toast, and the English table remains the fallback.
export default {
  en: {
    "chat.copyFailed": "Copy failed — the clipboard is not available.",
    "chat.copied": "Copied to clipboard",
  },
  "zh-CN": {
    "chat.copyFailed": "复制失败 — 剪贴板不可用。",
    "chat.copied": "已复制到剪贴板",
  },
  "zh-TW": {
    "chat.copyFailed": "複製失敗 — 剪貼簿無法使用。",
    "chat.copied": "已複製到剪貼簿",
  },
}
