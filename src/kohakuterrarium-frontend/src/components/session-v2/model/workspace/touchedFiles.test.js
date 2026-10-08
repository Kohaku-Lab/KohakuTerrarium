import { describe, expect, it } from "vitest"

import { collectTouched, shortPath, toolDiff, toolPath } from "./touchedFiles"

const tool = (name, args, extra = {}) => ({ type: "tool", name, args, status: "done", ...extra })

describe("collectTouched", () => {
  it("groups by action, keeps the latest per path, newest first, and walks sub-agent children", () => {
    const messagesByTab = {
      root: [
        { parts: [{ type: "text", content: "hi" }, tool("read", { path: "/a/x.py" })] },
        { parts: [tool("edit", { file_path: "/a/x.py", old_string: "a", new_string: "b" })] },
        {
          parts: [
            tool(
              "explore",
              {},
              { kind: "subagent", children: [tool("read", { path: "/a/y.py" })] },
            ),
            tool("write", { path: "/a/z.md" }, { status: "error" }),
          ],
        },
      ],
      "ch:tasks": [{ parts: [tool("read", { path: "/a/x.py" })] }],
    }
    const g = collectTouched(messagesByTab)
    expect(g.wrote.map((e) => e.path)).toEqual(["/a/x.py"])
    expect(g.wrote[0].diff).toEqual({ old: "a", new: "b" })
    expect(g.read.map((e) => e.path)).toEqual(["/a/x.py", "/a/y.py"])
    expect(g.errored.map((e) => e.path)).toEqual(["/a/z.md"])
    expect(g.exec).toEqual([])
  })

  it("ignores tools without a path and bash without a command", () => {
    const g = collectTouched({
      root: [{ parts: [tool("grep", { pattern: "x" }), tool("bash", { path: "/x" })] }],
    })
    expect(Object.values(g).flat()).toEqual([])
  })

  it("lists bash commands under exec by their first line, failures under errored", () => {
    const g = collectTouched({
      root: [
        {
          parts: [
            tool("bash", { command: "npm test\n--watch=false" }),
            tool("bash", { command: "rm -rf build" }, { status: "error" }),
            tool("bash", { command: "npm test" }),
          ],
        },
      ],
    })
    expect(g.exec.map((e) => [e.command, e.path])).toEqual([["npm test", ""]])
    expect(g.errored.map((e) => [e.command, e.path])).toEqual([["rm -rf build", ""]])
  })

  it("reads string-encoded args and shortens paths", () => {
    expect(toolPath({ args: '{"file_path":"/p/q"}' })).toBe("/p/q")
    expect(toolPath({ args: "not json" })).toBe("")
    expect(toolDiff({ args: { old_string: 1 } })).toBe(null)
    expect(shortPath("C:\\a\\b\\c\\d.txt")).toBe("b/c/d.txt")
  })
})
