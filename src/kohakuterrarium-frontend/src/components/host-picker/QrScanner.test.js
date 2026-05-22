/**
 * QrScanner test — exercises the URI parser via the exposed
 * ``parseKtConnect`` helper.  The optional Android JS bridge
 * scan path (``window.KohakuBridge.scanQr``) isn't runnable in
 * jsdom; the parse contract is the testable surface.
 */

import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import QrScanner from "./QrScanner.vue"

function parseFromComponent(uri) {
  const wrapper = mount(QrScanner)
  const fn = wrapper.vm.parseKtConnect
  return fn(uri)
}

describe("parseKtConnect", () => {
  it("decodes a well-formed http URI", () => {
    const out = parseFromComponent(
      "ktconnect://1.2.3.4:8001/?token=abc&scheme=http",
    )
    expect(out).toEqual({
      url: "http://1.2.3.4:8001",
      token: "abc",
      scheme: "http",
    })
  })

  it("decodes a well-formed https URI", () => {
    const out = parseFromComponent(
      "ktconnect://kt.home.lan:8001/?token=secret&scheme=https",
    )
    expect(out).toEqual({
      url: "https://kt.home.lan:8001",
      token: "secret",
      scheme: "https",
    })
  })

  it("defaults scheme to http when omitted", () => {
    const out = parseFromComponent("ktconnect://10.0.0.5:8001/?token=x")
    expect(out.scheme).toBe("http")
    expect(out.url).toBe("http://10.0.0.5:8001")
  })

  it("rejects wrong scheme", () => {
    expect(() => parseFromComponent("https://kt.home.lan?token=x")).toThrow(
      /ktconnect/,
    )
  })

  it("rejects missing token", () => {
    expect(() => parseFromComponent("ktconnect://host:8001/")).toThrow(/token/)
  })

  it("rejects gibberish", () => {
    expect(() => parseFromComponent("hello world")).toThrow()
  })

  it("URL-encoded token preserved literally after decode", () => {
    // URL parser decodes the percent-escape; we surface the
    // decoded value to the caller.
    const out = parseFromComponent("ktconnect://h:1/?token=abc%20def%2F")
    expect(out.token).toBe("abc def/")
  })

  it("rejects javascript: scheme smuggled via query param", () => {
    // Hostile QR: a malicious actor pre-computes a QR whose
    // ``scheme`` param is ``javascript``.  The parser must
    // reject — otherwise the WebView could end up at
    // javascript://kt.home.lan?token=... and execute attacker
    // code.  Audit fix.
    expect(() =>
      parseFromComponent("ktconnect://h:1/?token=x&scheme=javascript"),
    ).toThrow(/unsupported scheme/)
  })

  it("rejects file: scheme", () => {
    expect(() =>
      parseFromComponent("ktconnect://h:1/?token=x&scheme=file"),
    ).toThrow(/unsupported scheme/)
  })

  it("accepts only http and https case-insensitively", () => {
    const a = parseFromComponent("ktconnect://h:1/?token=x&scheme=HTTPS")
    expect(a.scheme).toBe("https")
    const b = parseFromComponent("ktconnect://h:1/?token=x&scheme=Http")
    expect(b.scheme).toBe("http")
  })
})
