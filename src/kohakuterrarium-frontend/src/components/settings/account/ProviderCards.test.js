import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import ElementPlus from "element-plus"

import { useLocaleStore } from "@/stores/locale"
import CodexUsageCard from "./CodexUsageCard.vue"
import GrokUsageCard from "./GrokUsageCard.vue"

const CAPTURED = Date.parse("2026-09-23T00:59:14Z") / 1000
const RESET = Date.parse("2026-09-28T15:08:17Z") / 1000
const EXPIRES = "2026-10-04T05:32:34.160668Z"
const compact = (epoch) =>
  new Date(epoch * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
const full = (epoch) => new Date(epoch * 1000).toLocaleString()

function card(component, usage) {
  return mount(component, { props: { usage }, global: { plugins: [ElementPlus] } })
}

function codexUsage(
  snapshots = [
    {
      limit_id: "codex",
      limit_name: "codex",
      plan_type: "pro",
      primary: { used_percent: 32, resets_at: RESET },
    },
  ],
) {
  return {
    status: "ok",
    captured_at: CAPTURED,
    snapshots,
    reset_credits: {
      credits: [
        {
          id: "r1",
          title: "Full reset",
          description: "Thanks for using Codex! One free reset.",
          expires_at: EXPIRES,
        },
      ],
    },
  }
}

function grokUsage(overrides = {}) {
  return {
    status: "ok",
    captured_at: CAPTURED,
    credential_source: "grok-cli",
    window: { period: "weekly", used_percent: 13, resets_at: RESET },
    products: [{ name: "GrokBuild", used_percent: 13 }],
    prepaid_balance: 0,
    ...overrides,
  }
}

describe("provider card hierarchy", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useLocaleStore().locale = "en"
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date(CAPTURED * 1000))
  })
  afterEach(() => vi.useRealTimers())

  it("keeps Codex quota and reset credits inside one visual card with a single default heading", async () => {
    const usage = codexUsage()
    const wrapper = card(CodexUsageCard, usage)
    expect(wrapper.classes()).toContain("card")
    expect(wrapper.element.querySelectorAll(".card")).toHaveLength(0)
    expect(wrapper.get("header").text()).toContain("Codex")
    expect(wrapper.get("header").text()).toContain("pro")
    expect(wrapper.findAll("[data-snapshot] h4")).toHaveLength(0)
    const credits = wrapper.get("[data-reset-credits]")
    expect(credits.classes()).toContain("border-solid")
    expect(credits.get("h4").text()).toBe("Codex reset credits")
    expect(credits.text()).toContain("1 available")
    expect(credits.element.closest("[data-provider]")).toBe(wrapper.element)
    const details = credits.get("details")
    expect(details.attributes("open")).toBeUndefined()
    expect(details.get("summary").text()).toBe("Details")
    expect(details.text()).toContain(usage.reset_credits.credits[0].description)
    const expiry = credits.get("time")
    expect(expiry.text()).toContain(compact(Date.parse(EXPIRES) / 1000))
    expect(expiry.text()).not.toContain("160668")
    expect(expiry.attributes("title")).toBe(full(Date.parse(EXPIRES) / 1000))
    await credits.get("[data-reset-redeem]").trigger("click")
    expect(wrapper.emitted("redeem")).toEqual([[usage.reset_credits.credits[0]]])
    expect(wrapper.get("footer time").attributes("title")).toBe(full(CAPTURED))
  })

  it("retains distinct limit names and plans when Codex returns multiple snapshots", () => {
    const wrapper = card(
      CodexUsageCard,
      codexUsage([
        { limit_id: "codex", limit_name: "codex", plan_type: "pro", primary: { used_percent: 32 } },
        {
          limit_id: "review",
          limit_name: "Code review",
          plan_type: "team",
          secondary: { used_percent: 21 },
        },
      ]),
    )
    const snapshots = wrapper.findAll("[data-snapshot]")
    expect(snapshots.map((item) => item.get("h4").text())).toEqual(["codex", "Code review"])
    expect(snapshots[0].text()).toContain("pro")
    expect(snapshots[1].text()).toContain("team")
    expect(wrapper.get("header").text()).not.toContain("pro")
  })

  it("shows a non-default named limit even when it is the only Codex snapshot", () => {
    const wrapper = card(
      CodexUsageCard,
      codexUsage([{ limit_id: "review", limit_name: "Code review", primary: { used_percent: 2 } }]),
    )
    expect(wrapper.get("[data-snapshot] h4").text()).toBe("Code review")
  })

  it("orders Grok quota, labeled breakdown and credits, then low-priority metadata", () => {
    const wrapper = card(GrokUsageCard, grokUsage())
    expect(wrapper.classes()).toContain("card")
    expect(wrapper.element.querySelectorAll(".card")).toHaveLength(0)
    const quota = wrapper.get("[data-quota]")
    expect(quota.text()).toContain("Weekly shared quota")
    expect(quota.get("[data-usage-bar]").classes()).toContain("bg-iolite")
    expect(quota.text()).toContain("13% used")
    expect(quota.text()).toContain("87% remaining")
    const reset = quota.get("time")
    expect(reset.text()).toContain(compact(RESET))
    expect(reset.attributes("title")).toBe(full(RESET))
    const breakdown = wrapper.get("[data-breakdown]")
    expect(breakdown.get("h4").text()).toBe("Usage breakdown")
    expect(breakdown.text()).toContain("Build")
    expect(breakdown.text()).toContain("13%")
    const extra = wrapper.get("[data-extra-credits]")
    expect(extra.get("dt").text()).toBe("Extra credits")
    expect(extra.get("dd").text()).toBe("0")
    expect(extra.element.parentElement).toBe(breakdown.element.parentElement)
    const footer = wrapper.get("footer")
    expect(footer.text()).toContain("Grok CLI sign-in")
    expect(footer.text()).not.toContain("grok-cli")
    expect(footer.get("time").text()).toContain(
      new Date(CAPTURED * 1000).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      }),
    )
    expect(footer.get("time").attributes("title")).toBe(full(CAPTURED))
    const order = wrapper
      .findAll("[data-quota], [data-usage-details], footer")
      .map((el) => el.element)
    expect(order).toEqual([
      quota.element,
      wrapper.get("[data-usage-details]").element,
      footer.element,
    ])
  })

  it("does not turn missing Grok metadata, balance or breakdown into invented values", () => {
    const wrapper = card(
      GrokUsageCard,
      grokUsage({
        captured_at: null,
        credential_source: null,
        prepaid_balance: null,
        products: [],
        window: { period: "weekly", used_percent: null, resets_at: null },
      }),
    )
    expect(wrapper.get("[data-breakdown]").text()).toContain("No product breakdown available")
    expect(wrapper.get("[data-extra-credits] dd").text()).toBe("Unknown")
    expect(wrapper.get("footer").text()).toContain("Update time unknown")
    expect(wrapper.get("footer").text()).not.toContain("Grok CLI sign-in")
    expect(wrapper.findAll("time")).toHaveLength(0)
    expect(wrapper.text()).not.toContain("100% remaining")
  })

  it.each(["zh-CN", "zh-TW"])("localizes the new groups in %s", (locale) => {
    useLocaleStore().locale = locale
    const grok = card(GrokUsageCard, grokUsage())
    const codex = card(CodexUsageCard, codexUsage())
    expect(grok.get("[data-breakdown] h4").text()).toBe(
      locale === "zh-CN" ? "用量构成" : "用量構成",
    )
    expect(codex.get("[data-reset-credits] h4").text()).toBe(
      locale === "zh-CN" ? "Codex 额度重置券" : "Codex 額度重設券",
    )
    expect(grok.text()).not.toContain("settings.account.")
    expect(codex.text()).not.toContain("settings.account.")
  })
})
