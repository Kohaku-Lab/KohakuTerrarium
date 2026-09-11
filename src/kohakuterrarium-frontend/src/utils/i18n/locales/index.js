import en from "./en"
import zhTW from "./zh-TW"
import zhCN from "./zh-CN"
import ja from "./ja"
import de from "./de"
import ko from "./ko"
import media from "./media"

// ``media`` carries genuine ``chat.media.*`` translations for en / zh-CN / zh-TW
// (the resolver's own locale set); the remaining dictionaries fall back to the
// English table through ``resolveMessage`` exactly like every other key.
export const messages = {
  en: { ...en, ...media.en },
  "zh-TW": { ...zhTW, ...media["zh-TW"] },
  "zh-CN": { ...zhCN, ...media["zh-CN"] },
  ja,
  de,
  ko,
}
