import { describe, expect, it } from "vitest"

import { baseName, languageOf } from "./languageOf"

describe("languageOf", () => {
  it("maps extensions, Dockerfile, and falls back to plaintext", () => {
    expect(languageOf("/a/b/main.py")).toBe("python")
    expect(languageOf("C:\\x\\App.VUE")).toBe("html")
    expect(languageOf("/r/Dockerfile")).toBe("dockerfile")
    expect(languageOf("/r/.gitignore")).toBe("plaintext")
    expect(languageOf("/r/notes.xyz")).toBe("plaintext")
  })

  it("takes the file name from either separator", () => {
    expect(baseName("/a/b/c.txt")).toBe("c.txt")
    expect(baseName("C:\\a\\d.md")).toBe("d.md")
  })
})
