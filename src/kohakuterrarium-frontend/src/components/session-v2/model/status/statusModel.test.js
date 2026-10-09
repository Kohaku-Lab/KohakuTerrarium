import { describe, expect, it } from "vitest"

import {
  agentRows,
  channelRows,
  contextRows,
  contextTone,
  formatTokens,
  formatUptime,
  jobOwnerTab,
  jobRows,
  percentOf,
  usageRows,
} from "./statusModel"

describe("status figures", () => {
  it("formats tokens and uptime", () => {
    expect(formatTokens(950)).toBe("950")
    expect(formatTokens(12_345)).toBe("12.3K")
    expect(formatTokens(2_500_000)).toBe("2.5M")
    expect(formatTokens(400_000)).toBe("400K")
    expect(formatTokens(1_000_000)).toBe("1M")
    expect(formatTokens(999_960)).toBe("1M")
    expect(formatTokens(0)).toBe("0")
    const now = Date.parse("2026-10-08T12:00:00Z")
    expect(formatUptime("2026-10-08T11:59:18Z", now)).toBe("42s")
    expect(formatUptime("2026-10-08T08:55:00Z", now)).toBe("3h 05m")
    expect(formatUptime("2026-10-06T08:00:00Z", now)).toBe("2d 4h")
    expect(formatUptime(null, now)).toBe("—")
  })

  it("clamps percentages and picks a tone", () => {
    expect(percentOf(50, 200)).toBe(25)
    expect(percentOf(500, 200)).toBe(100)
    expect(percentOf(10, 0)).toBe(0)
    expect([contextTone(10), contextTone(65), contextTone(90)]).toEqual(["ok", "warn", "bad"])
  })
})

describe("status rows", () => {
  const instance = {
    creatures: [
      {
        name: "lead",
        running: true,
        is_privileged: true,
        send_channels: ["tasks"],
        listen_channels: ["report"],
      },
      {
        name: "worker",
        running: false,
        llm_name: "a/b",
        send_channels: ["report"],
        listen_channels: ["tasks"],
      },
    ],
    channels: [{ name: "tasks", type: "broadcast" }, { name: "report" }, { name: "worker" }],
  }

  it("reads the privileged node's usage, job and model from the store's root tab", () => {
    const rows = agentRows(instance, {
      tokenUsage: { root: { prompt: 10, completion: 5 }, lead: { prompt: 999 } },
      runningJobs: {
        j1: { name: "bash", tab: "root", startedAt: 1 },
        j2: { name: "grep", tab: "root", startedAt: 5 },
        j3: { name: "x", tab: "elsewhere", startedAt: 9 },
      },
      modelByTab: { root: { llmName: "codex/gpt" } },
      rootName: "lead",
    })
    expect(
      rows.map((r) => [r.name, r.key, r.status, r.model, r.tokens, r.job?.id || null]),
    ).toEqual([
      ["lead", "root", "running", "codex/gpt", 15, "j2"],
      ["worker", "worker", "stopped", "a/b", 0, null],
    ])
    expect(rows[0].privileged).toBe(true)
  })

  it("lists designed channels with senders and listeners, dropping creature aliases", () => {
    expect(channelRows(instance)).toEqual([
      { name: "tasks", kind: "broadcast", senders: ["lead"], listeners: ["worker"] },
      { name: "report", kind: "", senders: ["worker"], listeners: ["lead"] },
    ])
  })

  it("ranks token sources heaviest first and drops idle ones", () => {
    const rows = usageRows({
      root: { prompt: 100, completion: 20, cached: 40, lastPrompt: 60 },
      worker: { prompt: 500, completion: 50, total: 550, lastPrompt: 300 },
      idle: { prompt: 0, completion: 0 },
    })
    expect(rows.map((r) => [r.source, r.total, r.cached, r.lastPrompt])).toEqual([
      ["worker", 550, 0, 300],
      ["root", 120, 40, 60],
    ])
    expect(usageRows(undefined)).toEqual([])
  })

  it("measures each creature's context against its own window, privileged first", () => {
    const rows = contextRows(
      {
        max_context: 100_000,
        creatures: [
          { name: "worker", max_context: 32_000 },
          { name: "lead", is_privileged: true },
          { name: "idle" },
        ],
      },
      {
        tokenUsage: { worker: { lastPrompt: 16_000 }, root: { lastPrompt: 90_000 } },
        modelByTab: { root: { maxContext: 200_000, compactThreshold: 100_000 } },
        fallback: { maxContext: 100_000 },
        rootName: "lead",
      },
    )
    expect(rows.map((r) => [r.name, r.maxContext, r.pct, r.compactPct, r.tone])).toEqual([
      ["lead", 200_000, 45, 50, "bad"],
      ["worker", 32_000, 50, 0, "ok"],
      ["idle", 100_000, 0, 0, "ok"],
    ])
  })

  it("finds a job's owning tab, falling back to the first creature conversation", () => {
    expect(jobOwnerTab({ tab: "worker" }, ["root"], [])).toBe("worker")
    expect(jobOwnerTab({}, ["ch:tasks", "solo"], [{ name: "x" }])).toBe("solo")
    expect(jobOwnerTab({}, [], [{ name: "x" }])).toBe("x")
  })

  it("sorts running jobs newest first", () => {
    const rows = jobRows({
      a: { name: "a", startedAt: 1 },
      b: { name: "b", startedAt: 3, cancelling: true },
    })
    expect(rows.map((r) => [r.id, r.cancelling])).toEqual([
      ["b", true],
      ["a", false],
    ])
  })
})
