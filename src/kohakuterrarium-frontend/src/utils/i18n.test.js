import { describe, expect, it } from "vitest"

import { translate, translatePanelLabel, translatePresetLabel } from "./i18n"
import { LOCALE_DISPLAY_NAMES } from "@/stores/locale"

describe("i18n helpers", () => {
  it("translates plain string keys", () => {
    expect(translate("zh-CN", "common.settings")).toBe("设置")
  })

  it("interpolates message parameters", () => {
    expect(translate("zh-CN", "sessions.total", { count: 12 })).toBe("共 12 个会话")
  })

  it("falls back to english for unknown locale", () => {
    expect(translate("fr", "common.home")).toBe("Home")
  })

  it("supports german translations and english fallback for missing german keys", () => {
    expect(translate("de", "common.settings")).toBe("Einstellungen")
    expect(translate("de", "chat.sendMessage")).toBe("Send message")
  })

  it("uses stable locale display names for the language selector", () => {
    expect(LOCALE_DISPLAY_NAMES.en).toBe("English")
    expect(LOCALE_DISPLAY_NAMES["zh-CN"]).toBe("简体中文")
    expect(LOCALE_DISPLAY_NAMES.de).toBe("Deutsch")
  })

  it("translates registered panel and preset labels", () => {
    expect(translatePanelLabel("zh-CN", "chat")).toBe("聊天")
    expect(translatePresetLabel("zh-CN", "chat-focus")).toBe("聊天聚焦")
  })
})
