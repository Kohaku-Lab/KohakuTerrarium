// Shared link strings for the host-neutral opener (``chat.link.*``).
//
// Kept as one small table merged into each locale by ``./index.js`` so the
// supported dictionaries carry genuine link text instead of falling back to a
// hardcoded English string. Only the failure the Host reports after a user
// click lives here; the opener (``platformLink.js``) surfaces it verbatim, and
// the English value is also the fallback for a partially translated locale.
export default {
  en: {
    "chat.link.openFailed": "Could not open link",
    "chat.link.unavailable": "Link unavailable",
  },
  "zh-CN": {
    "chat.link.openFailed": "无法打开链接",
    "chat.link.unavailable": "链接不可用",
  },
  "zh-TW": {
    "chat.link.openFailed": "無法開啟連結",
    "chat.link.unavailable": "連結無法使用",
  },
}
