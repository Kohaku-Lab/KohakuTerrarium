// Shared slash-menu failure string (``chat.slash.error``).
//
// Kept as one small table merged into each locale by ``./index.js`` so the one
// production SlashCommandMenu — rendered by BOTH the Dashboard and the VS Code
// webview — surfaces a genuine inventory failure instead of folding it into the
// empty state (``chat.slash.empty``). The English value is also the fallback for
// a partially translated locale.
export default {
  en: {
    "chat.slash.error": "Could not load commands and skills",
  },
  "zh-CN": {
    "chat.slash.error": "无法加载命令和技能",
  },
  "zh-TW": {
    "chat.slash.error": "無法載入命令與技能",
  },
}
