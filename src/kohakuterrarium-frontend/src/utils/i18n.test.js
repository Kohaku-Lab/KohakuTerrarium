import { describe, expect, it } from "vitest"

import { LOCALE_DISPLAY_NAMES } from "@/stores/locale"

import {
  translate,
  translatePanelDescription,
  translatePanelLabel,
  translatePresetLabel,
} from "./i18n"

describe("i18n helpers", () => {
  it("translates plain string keys", () => {
    expect(translate("zh-CN", "common.settings")).toBe("设置")
    expect(translate("zh-TW", "common.settings")).toBe("設定")
    expect(translate("ja", "common.settings")).toBe("設定")
    expect(translate("ko", "common.settings")).toBe("설정")
  })

  it("interpolates message parameters", () => {
    expect(translate("zh-CN", "sessions.total", { count: 12 })).toBe("共 12 个会话")
    expect(translate("zh-TW", "sessions.total", { count: 12 })).toBe("共 12 個工作階段")
    expect(translate("ja", "sessions.total", { count: 12 })).toBe("全 12 件のセッション")
    expect(translate("ko", "sessions.total", { count: 12 })).toBe("총 12개 세션")
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
    expect(LOCALE_DISPLAY_NAMES["zh-TW"]).toBe("繁體中文")
    expect(LOCALE_DISPLAY_NAMES["zh-CN"]).toBe("简体中文")
    expect(LOCALE_DISPLAY_NAMES.ja).toBe("日本語")
    expect(LOCALE_DISPLAY_NAMES.de).toBe("Deutsch")
    expect(LOCALE_DISPLAY_NAMES.ko).toBe("한국어")
  })

  it("translates registered panel and preset labels", () => {
    expect(translatePanelLabel("zh-CN", "chat")).toBe("聊天")
    expect(translatePresetLabel("zh-CN", "chat-focus")).toBe("聊天聚焦")
    expect(translatePanelLabel("ja", "chat")).toBe("チャット")
    expect(translatePanelLabel("ko", "chat")).toBe("채팅")
  })

  it("keeps the two status panels and the two activity panels apart in every locale", () => {
    for (const locale of ["en", "zh-CN", "zh-TW", "ja", "ko"]) {
      expect(translatePanelLabel(locale, "status-dashboard")).not.toBe(
        translatePanelLabel(locale, "status-tab"),
      )
      expect(translatePanelLabel(locale, "activity")).not.toBe(
        translatePanelLabel(locale, "editor-status"),
      )
    }
    expect(translatePanelLabel("en", "status-dashboard")).toBe("Overview")
    expect(translatePanelLabel("en", "activity")).toBe("Jobs")
    expect(translatePanelLabel("en", "state")).toBe("Creature State")
  })

  it("describes every user-facing panel in every locale", () => {
    const ids = [
      "chat",
      "status-dashboard",
      "status-tab",
      "monaco-editor",
      "files",
      "activity",
      "settings",
      "state",
      "creatures",
      "canvas",
      "debug",
      "terminal",
      "modules",
      "drives",
    ]
    for (const locale of ["en", "zh-CN", "zh-TW", "ja", "ko"]) {
      for (const id of ids) {
        expect(translatePanelDescription(locale, id), `${locale}/${id}`).toBeTruthy()
      }
    }
    // Hidden aliases carry no description.
    expect(translatePanelDescription("en", "file-tree", "")).toBe("")
  })
})
