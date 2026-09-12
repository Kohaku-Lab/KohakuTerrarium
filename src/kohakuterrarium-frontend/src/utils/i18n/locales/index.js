import en from "./en"
import zhTW from "./zh-TW"
import zhCN from "./zh-CN"
import ja from "./ja"
import de from "./de"
import ko from "./ko"
import media from "./media"
import links from "./links"
import slash from "./slash"

// ``media``/``links``/``slash`` carry genuine ``chat.media.*`` / ``chat.link.*`` /
// ``chat.slash.error`` translations for en / zh-CN / zh-TW (the resolver's own
// locale set); the remaining dictionaries fall back to the English table through
// ``resolveMessage`` exactly like every other key.
export const messages = {
  en: { ...en, ...media.en, ...links.en, ...slash.en },
  "zh-TW": { ...zhTW, ...media["zh-TW"], ...links["zh-TW"], ...slash["zh-TW"] },
  "zh-CN": { ...zhCN, ...media["zh-CN"], ...links["zh-CN"], ...slash["zh-CN"] },
  ja,
  de,
  ko,
}
