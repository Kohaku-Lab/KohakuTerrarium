import { describe, expect, it } from "vitest"

import { moduleDescription, modulePresentation } from "./modulePresentation"
import { SUPPORTED_LOCALES } from "@/stores/locale"
import { translate } from "@/utils/i18n"
import strings from "@/utils/i18n/locales/settingsModules"

const tFor = (locale) => (key, params) => translate(locale, key, params)

describe("module settings translations", () => {
  it("covers every new message in every supported language with matching placeholders", () => {
    const placeholders = (value) =>
      [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort()
    for (const locale of SUPPORTED_LOCALES) {
      expect(Object.keys(strings[locale]).sort()).toEqual(Object.keys(strings.en).sort())
      for (const [key, message] of Object.entries(strings[locale])) {
        expect(typeof message, `${locale}/${key}`).toBe("string")
        expect(message.trim(), `${locale}/${key}`).not.toBe("")
        expect(placeholders(message), `${locale}/${key}`).toEqual(placeholders(strings.en[key]))
        expect(translate(locale, key), `${locale}/${key}`).not.toBe(key)
      }
    }
  })

  it.each(SUPPORTED_LOCALES)(
    "localizes built-in help and option labels in %s without changing the schema",
    (locale) => {
      const t = tFor(locale)
      const module = {
        type: "plugin",
        name: "sandbox",
        description: "Original sandbox description",
        schema: {
          backend: {
            type: "enum",
            values: ["auto", "audit", "off"],
            default: "auto",
            doc: "Original help",
          },
          future_option: null,
        },
      }
      const before = JSON.stringify(module)
      const layout = modulePresentation(module, { backend: "auto" }, t)
      expect(moduleDescription(module, t)).not.toBe(module.description)
      expect(layout.fields.backend.label).toBe(
        strings[locale]["settings.moduleInfo.plugin.sandbox.backend.label"],
      )
      expect(layout.fields.backend.doc).toBe(
        strings[locale]["settings.moduleInfo.plugin.sandbox.backend.doc"],
      )
      expect(layout.fields.backend.labels.audit).toBe(strings[locale]["settings.moduleValue.audit"])
      expect(JSON.stringify(module)).toBe(before)
      expect(layout.fields.future_option).toBeDefined()
    },
  )

  it("keeps custom labels, descriptions, enum values and diagnostic messages intact", () => {
    const module = {
      type: "tool",
      name: "custom",
      description: "Author's own description",
      schema: {
        backend: {
          type: "enum",
          label: "Author's label",
          description: "Author's help",
          values: ["auto"],
          labels: { auto: "Author's choice" },
          disabled_values: { auto: "Provider-specific diagnostic" },
        },
      },
    }
    const layout = modulePresentation(module, {}, tFor("zh-CN"))
    expect(moduleDescription(module, tFor("zh-CN"))).toBe(module.description)
    expect(layout.fields.backend).toMatchObject({
      label: "Author's label",
      doc: "Author's help",
      labels: { auto: "Author's choice" },
      disabledValues: { auto: "Provider-specific diagnostic" },
    })
    expect(layout.autoSave).toBe("修改后自动保存")
  })

  it.each(SUPPORTED_LOCALES)(
    "localizes known connection notices in %s and preserves commands and unknown errors",
    (locale) => {
      const module = {
        type: "tool",
        name: "web_search",
        schema: {
          backend: {
            type: "enum",
            values: ["duckduckgo", "codex", "deepseek", "deepseek_api"],
            disabled_values: {
              deepseek: "DeepSeek API key is not configured. Run: kt config key set deepseek",
              deepseek_api: "DeepSeek API key is not configured. Run: kt config key set deepseek",
              codex: "Codex subscription is not connected. Run: kt login codex",
              duckduckgo: "Unknown operational error",
            },
          },
        },
      }
      const layout = modulePresentation(module, { backend: "codex" }, tFor(locale))
      expect(layout.fields.backend.disabledValues.deepseek).toContain("kt config key set deepseek")
      expect(layout.fields.backend.disabledValues.deepseek).toBe(
        layout.fields.backend.disabledValues.deepseek_api,
      )
      expect(layout.fields.backend.disabledValues.codex).toContain("kt login codex")
      expect(layout.fields.backend.disabledValues.duckduckgo).toBe("Unknown operational error")
      expect(layout.fields.backend.labels.codex).toBe("Codex")
    },
  )
})
