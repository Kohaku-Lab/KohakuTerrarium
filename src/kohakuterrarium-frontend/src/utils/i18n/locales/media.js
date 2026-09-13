// Shared media strings for the host-neutral resolver (``chat.media.*``).
//
// Kept as one small table merged into each locale by ``./index.js`` so every
// supported dictionary carries genuine media text instead of silently falling
// back to the English table. The English values are also the resolver's
// fallback, so a partially translated locale still reads coherently.
//
// ``open``/``save``/``videoUnavailable``/``unavailable`` label the production
// media leaves (``VideoFilePreview.vue``, ``MediaPreview.js``) that BOTH hosts
// render, so a Chinese locale shows real controls instead of the hardcoded
// English defaults those components used to carry.
export default {
  en: {
    "chat.media.notReady": "Wait for the Session to be ready before loading media",
    "chat.media.prepareFailed": "Could not load {name}",
    "chat.media.superseded": "Media ownership changed",
    "chat.media.loading": "Loading media…",
    "chat.media.open": "Open",
    "chat.media.save": "Save",
    "chat.media.videoUnavailable": "This video could not be played",
    "chat.media.unavailable": "Media unavailable",
  },
  "zh-CN": {
    "chat.media.notReady": "请等待会话就绪后再加载媒体",
    "chat.media.prepareFailed": "无法加载 {name}",
    "chat.media.superseded": "媒体归属已变更",
    "chat.media.loading": "正在加载媒体…",
    "chat.media.open": "打开",
    "chat.media.save": "保存",
    "chat.media.videoUnavailable": "此视频无法播放",
    "chat.media.unavailable": "媒体不可用",
  },
  "zh-TW": {
    "chat.media.notReady": "請等待工作階段就緒後再載入媒體",
    "chat.media.prepareFailed": "無法載入 {name}",
    "chat.media.superseded": "媒體歸屬已變更",
    "chat.media.loading": "正在載入媒體…",
    "chat.media.open": "開啟",
    "chat.media.save": "儲存",
    "chat.media.videoUnavailable": "此影片無法播放",
    "chat.media.unavailable": "媒體無法使用",
  },
}
