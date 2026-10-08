import { describe, expect, it } from "vitest"

import { LOCALES, V2_STRINGS, translate } from "./v2Strings"

const SOURCES = Object.entries({
  ...import.meta.glob("../**/*.{vue,js}", { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob("../../settings/SettingsPage.vue", {
    query: "?raw",
    import: "default",
    eager: true,
  }),
})
  .filter(([path]) => !path.endsWith(".test.js") && !path.includes("/strings/"))
  .map(([path, text]) => ({ path, text }))

const placeholders = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

// Template-literal keys such as `widget.${id}.title`, as patterns over the key space.
const DYNAMIC = SOURCES.flatMap(({ text }) =>
  [...text.matchAll(/`([a-z][\w-]*(?:\.[\w-]*|\$\{[^}]+\})+)`/g)]
    .map((m) => m[1])
    .filter((k) => k.includes("${"))
    .map(
      (k) =>
        new RegExp(
          `^${k
            .split(/\$\{[^}]+\}/)
            .map(escape)
            .join("[\\w-]+")}$`,
        ),
    ),
)
const LITERALS = new Set(
  SOURCES.flatMap(({ text }) => [...text.matchAll(/["'`]([a-z][\w.-]*)["'`]/g)].map((m) => m[1])),
)
// Literal-key calls of whatever name each file binds to useV2T().
const CALLED = SOURCES.flatMap(({ path, text }) =>
  [...text.matchAll(/const (\w+) = useV2T\(\)/g)].flatMap(([, name]) =>
    [...text.matchAll(new RegExp(`\\b${name}\\(\\s*["']([\\w.-]+)["']`, "g"))].map((m) => ({
      path,
      key: m[1],
    })),
  ),
)

describe("v2 strings", () => {
  it("has every English key in each locale", () => {
    const keys = Object.keys(V2_STRINGS.en)
    for (const locale of LOCALES.filter((l) => l !== "en"))
      expect(
        keys.filter((k) => !V2_STRINGS[locale][k]),
        locale,
      ).toEqual([])
  })

  it("has no locale-only keys and the same placeholders in every locale", () => {
    for (const locale of LOCALES.filter((l) => l !== "en")) {
      expect(
        Object.keys(V2_STRINGS[locale]).filter((k) => !(k in V2_STRINGS.en)),
        locale,
      ).toEqual([])
      const drift = Object.keys(V2_STRINGS.en).filter(
        (k) => placeholders(V2_STRINGS.en[k]).join() !== placeholders(V2_STRINGS[locale][k]).join(),
      )
      expect(drift, locale).toEqual([])
    }
  })

  it("defines every key the shell's source asks for", () => {
    expect(SOURCES.length).toBeGreaterThan(40)
    expect(CALLED.length).toBeGreaterThan(100)
    expect(CALLED.filter(({ key }) => !(key in V2_STRINGS.en))).toEqual([])
  })

  it("defines no key the source never uses", () => {
    const unused = Object.keys(V2_STRINGS.en).filter(
      (k) => !LITERALS.has(k) && !DYNAMIC.some((re) => re.test(k)),
    )
    expect(unused).toEqual([])
  })

  it("falls back to English, then to the key, and fills placeholders", () => {
    expect(translate("ja", "tab.chat")).toBe("Chat")
    expect(translate("zh-TW", "tab.chat")).toBe("聊天")
    expect(translate("en", "no.such.key")).toBe("no.such.key")
    expect(translate("en", "hero.title", { name: "root" })).toBe("What should root work on?")
  })
})
