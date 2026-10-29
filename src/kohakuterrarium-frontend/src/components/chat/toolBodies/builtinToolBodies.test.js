import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import { builtinToolBody } from "./builtinToolBodies.js"
import FilePreviewBody from "./FilePreviewBody.vue"

const call = (name, preview, extra = {}) => ({
  name,
  status: "done",
  result: "Wrote 1 line",
  resultMeta: preview ? { canvas_preview: preview } : null,
  ...extra,
})

describe("built-in tool bodies", () => {
  it("write / edit / multi_edit with a text preview get the file preview body", () => {
    const preview = { kind: "write", file_path: "a.py", lang: "python", content: "x = 1" }
    for (const name of ["write", "edit", "multi_edit"])
      expect(builtinToolBody(call(name, preview))).not.toBeNull()
    expect(builtinToolBody(call("write", null))).toBeNull()
    expect(
      builtinToolBody(call("canvas_image", { kind: "image", file_path: "a.png", content: "/x" })),
    ).toBeNull()
    expect(builtinToolBody(call("bash", preview))).toBeNull()
  })

  it("shows the path, the new content and the tool output", () => {
    const w = mount(FilePreviewBody, {
      props: {
        call: call("edit", {
          kind: "edit",
          file_path: "src/a.py",
          lang: "python",
          content: "x = 2",
        }),
      },
    })
    expect(w.text()).toContain("src/a.py")
    expect(w.find('[data-test="file-preview-content"]').text()).toContain("x = 2")
    expect(w.find('[data-test="file-preview-output"]').text()).toBe("Wrote 1 line")
  })

  it("says when the file was too large to preview", () => {
    const w = mount(FilePreviewBody, {
      props: { call: call("write", { kind: "write", file_path: "big.bin", content: null }) },
    })
    expect(w.find('[data-test="file-preview-too-large"]').exists()).toBe(true)
  })
})
