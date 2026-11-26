import { describe, expect, it } from "vitest"

import MarkdownIt from "markdown-it"

import {
  BACKEND_PATH_PREFIXES,
  applyExternalLinkRule,
  isBackendResourceUrl,
  isImageUrl,
  resolvePlatformLink,
  shouldOpenThroughHost,
} from "./externalLinks.js"

// Browsers in this suite serve from http://localhost/ (see vitest.config.js).
const DASHBOARD = "http://localhost"

describe("resolvePlatformLink — shared card/link policy", () => {
  it("keeps an absolute https link and marks it external", () => {
    expect(resolvePlatformLink("https://example.test/docs")).toEqual({
      href: "https://example.test/docs",
      external: true,
    })
  })

  it("keeps a same-origin absolute link in-app", () => {
    expect(resolvePlatformLink(`${DASHBOARD}/sessions/a`, DASHBOARD)).toEqual({
      href: `${DASHBOARD}/sessions/a`,
      external: false,
    })
  })

  it("resolves a relative path against the explicit platform origin", () => {
    // Regression guard: a relative card URL used to be silently dropped by the
    // old http-only filter; against an explicit origin it resolves as before.
    expect(resolvePlatformLink("/sessions/abc", DASHBOARD)).toEqual({
      href: `${DASHBOARD}/sessions/abc`,
      external: false,
    })
    expect(resolvePlatformLink("cards/one", "https://backend.test")).toEqual({
      href: "https://backend.test/cards/one",
      external: false,
    })
  })

  it("keeps a hash anchor in-page", () => {
    expect(resolvePlatformLink("#section", DASHBOARD)).toEqual({
      href: "#section",
      external: false,
    })
  })

  it("drops a javascript: target instead of rendering a live link", () => {
    expect(resolvePlatformLink("javascript:alert(1)", DASHBOARD)).toEqual({ href: null })
  })

  it("drops a data: target", () => {
    expect(resolvePlatformLink("data:text/html,<h1>x</h1>", DASHBOARD)).toEqual({ href: null })
  })

  it("keeps mailto: for the OS", () => {
    expect(resolvePlatformLink("mailto:team@example.test", DASHBOARD).href).toBe(
      "mailto:team@example.test",
    )
  })

  it("reports a relative path as UNAVAILABLE when the host has no origin", () => {
    expect(resolvePlatformLink("/sessions/abc", null)).toEqual({ href: null, unavailable: true })
    expect(resolvePlatformLink("./card", undefined)).toEqual({ href: null, unavailable: true })
  })

  it("still renders an absolute https link when the host has no origin", () => {
    expect(resolvePlatformLink("https://example.test", null)).toEqual({
      href: "https://example.test/",
      external: true,
    })
  })

  it("treats a non-string / empty target as no link", () => {
    expect(resolvePlatformLink(null, DASHBOARD)).toEqual({ href: null })
    expect(resolvePlatformLink("   ", DASHBOARD)).toEqual({ href: null })
  })
})

describe("shouldOpenThroughHost — who owns the click", () => {
  it("routes a relative reference and an absolute http(s) URL to the host opener", () => {
    expect(shouldOpenThroughHost("/sessions/abc")).toBe(true)
    expect(shouldOpenThroughHost("cards/one?q=1#top")).toBe(true)
    expect(shouldOpenThroughHost("https://example.test/docs")).toBe(true)
    expect(shouldOpenThroughHost("http://example.test")).toBe(true)
  })

  it("keeps in-page hashes and OS schemes out of the host opener", () => {
    expect(shouldOpenThroughHost("#section")).toBe(false)
    expect(shouldOpenThroughHost("mailto:team@example.test")).toBe(false)
    expect(shouldOpenThroughHost("tel:+123")).toBe(false)
  })

  it("never hands an unsafe scheme to the opener", () => {
    expect(shouldOpenThroughHost("javascript:alert(1)")).toBe(false)
    expect(shouldOpenThroughHost("data:text/html,x")).toBe(false)
    expect(shouldOpenThroughHost("command:foo")).toBe(false)
    expect(shouldOpenThroughHost("")).toBe(false)
    expect(shouldOpenThroughHost(null)).toBe(false)
  })
})

describe("isBackendResourceUrl — backend files are not app pages", () => {
  it("keeps the prefix list in one place", () => {
    expect(BACKEND_PATH_PREFIXES).toEqual(["/api/", "/ws/"])
  })

  it("matches same-origin /api/ paths, relative or absolute", () => {
    expect(isBackendResourceUrl("/api/sessions/s/artifacts/x.jpg", DASHBOARD)).toBe(true)
    expect(isBackendResourceUrl(`${DASHBOARD}/api/sessions/s/artifacts/x.zip`, DASHBOARD)).toBe(
      true,
    )
  })

  it("leaves app routes, foreign origins and look-alike paths out", () => {
    expect(isBackendResourceUrl("/sessions/abc", DASHBOARD)).toBe(false)
    expect(isBackendResourceUrl("/apix/a", DASHBOARD)).toBe(false)
    expect(isBackendResourceUrl("https://example.test/api/x", DASHBOARD)).toBe(false)
    expect(isBackendResourceUrl("mailto:a@example.test", DASHBOARD)).toBe(false)
  })

  it("only accepts a root-relative path when the origin is unknown", () => {
    expect(isBackendResourceUrl("/api/x.png", null)).toBe(true)
    expect(isBackendResourceUrl("api/x.png", null)).toBe(false)
    expect(isBackendResourceUrl("//example.test/api/x.png", null)).toBe(false)
    expect(isBackendResourceUrl("https://example.test/api/x.png", null)).toBe(false)
  })
})

describe("isImageUrl", () => {
  it("recognises image extensions on the path only", () => {
    expect(isImageUrl("/api/a/pose-overview.JPG")).toBe(true)
    expect(isImageUrl("/api/a/b.webp?v=1")).toBe(true)
    expect(isImageUrl("/api/a/b.zip")).toBe(false)
    expect(isImageUrl("/api/a/b.zip?name=x.png")).toBe(false)
    expect(isImageUrl(null)).toBe(false)
  })
})

describe("applyExternalLinkRule with an explicit origin", () => {
  const md = applyExternalLinkRule(new MarkdownIt(), DASHBOARD)

  it("targets backend file links _blank and leaves app routes in-app", () => {
    expect(md.render("[f](/api/sessions/s/artifacts/a.zip)")).toContain('target="_blank"')
    expect(md.render("[s](/sessions/abc)")).not.toContain("target=")
  })
})
