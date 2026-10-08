import { describe, expect, it } from "vitest"
import { computed, reactive } from "vue"

import {
  eventPreview,
  eventRows,
  filterRows,
  lineDiff,
  logRows,
  rowKinds,
  rowPayload,
  safeJson,
  traceRows,
} from "./debugModel"

describe("debug rows", () => {
  it("merges every conversation's messages newest first, keeping order for equal times", () => {
    const rows = eventRows({
      root: [
        { id: 1, role: "user", content: "hi", timestamp: "2026-10-08T10:00:00Z" },
        {
          id: 2,
          role: "assistant",
          parts: [{ type: "text", content: "hello" }],
          timestamp: "2026-10-08T10:00:05Z",
        },
      ],
      "ch:tasks": [{ id: 3, role: "channel", content: "go", timestamp: "2026-10-08T10:00:03Z" }],
    })
    expect(rows.map((r) => r.key)).toEqual(["root:2", "ch:tasks:3", "root:1"])
    expect(rows[0].preview).toBe("hello")
    expect(rows.map((r) => r.tab)).toEqual(["root", "#tasks", "root"])
  })

  it("labels the privileged node's root conversation with its real name", () => {
    const rows = eventRows(
      {
        root: [
          { id: 1, role: "user", content: "hi" },
          { id: 2, role: "user", content: "x" },
        ],
      },
      "lead",
    )
    expect(rows.map((r) => [r.key, r.tab])).toEqual([
      ["root:2", "lead"],
      ["root:1", "lead"],
    ])
    expect(filterRows(rows, { query: "lead" })).toHaveLength(2)
  })

  it("previews text content, content arrays and tool parts", () => {
    expect(eventPreview({ content: [{ type: "text", text: "a" }, { type: "image" }] })).toBe("a")
    expect(eventPreview({ parts: [{ type: "tool", name: "bash" }] })).toBe("⚙ bash")
    expect(eventPreview({ summary: "compacted" })).toBe("compacted")
  })

  it("lists one conversation's tool calls newest first", () => {
    const rows = traceRows([
      { parts: [{ type: "tool", id: "a", name: "read", status: "done", result: "ok" }] },
      {
        parts: [
          { type: "text" },
          { type: "tool", id: "b", name: "explore", kind: "subagent", status: "running" },
        ],
      },
    ])
    expect(rows.map((r) => [r.key, r.kind, r.status])).toEqual([
      ["b", "subagent", "running"],
      ["a", "tool", "done"],
    ])
  })

  it("filters by kind and case-insensitive text, and lists kinds", () => {
    const rows = logRows([
      { level: "info", module: "kt.api", text: "Started" },
      { level: "error", module: "kt.llm", text: "Rate limited" },
    ])
    expect(filterRows(rows, { query: "RATE" }).map((r) => r.module)).toEqual(["kt.llm"])
    expect(filterRows(rows, { kind: "info" }).map((r) => r.module)).toEqual(["kt.api"])
    expect(filterRows(rows)).toBe(rows)
    expect(rowKinds(rows)).toEqual(["error", "info"])
  })

  it("keys log rows by line sequence so a trimmed buffer keeps row identity", () => {
    const lines = [1, 2, 3].map((seq) => ({ seq, level: "info", text: `l${seq}` }))
    const first = logRows(lines)
    const trimmed = logRows(lines.slice(1))
    expect(trimmed.map((r) => r.key)).toEqual(["log:2", "log:3"])
    expect(trimmed[0]).toBe(first[1])
  })

  it("follows a streaming tail message without rebuilding older rows", () => {
    const tail = reactive({ id: 2, role: "assistant", parts: [{ type: "text", content: "he" }] })
    const byTab = reactive({ root: [{ id: 1, role: "user", content: "hi" }, tail] })
    const rows = computed(() => eventRows(byTab))
    const before = rows.value
    expect(before.find((r) => r.key === "root:2").preview).toBe("he")
    tail.parts[0].content = "hello"
    const after = rows.value
    expect(after.find((r) => r.key === "root:2").preview).toBe("hello")
    expect(after.find((r) => r.key === "root:1")).toBe(before.find((r) => r.key === "root:1"))
  })

  it("serialises payloads safely", () => {
    const a = { name: "x" }
    a.self = a
    expect(safeJson(rowPayload({ message: a }))).toContain("[circular]")
  })
})

describe("lineDiff", () => {
  const kinds = (a, b) => lineDiff(a, b).map((l) => `${l.kind}:${l.text}`)

  it("keeps lines that only shifted position", () => {
    expect(kinds("a\nb\nc", "new\na\nb\nc")).toEqual(["add:new", "same:a", "same:b", "same:c"])
  })

  it("marks a changed line as removed then added, and keeps repeated lines aligned", () => {
    expect(kinds("x\nsame\nx", "x\nother\nx")).toEqual([
      "same:x",
      "del:same",
      "add:other",
      "same:x",
    ])
    expect(kinds("a\na", "a")).toEqual(["same:a", "del:a"])
  })
})
