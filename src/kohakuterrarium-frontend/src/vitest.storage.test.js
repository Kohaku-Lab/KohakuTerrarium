import { describe, expect, it } from "vitest"

describe("test environment web storage", () => {
  it("keeps values written to localStorage until cleared", () => {
    localStorage.clear()
    localStorage.setItem("kt.test.key", "value")
    expect(localStorage.getItem("kt.test.key")).toBe("value")
    expect(localStorage.length).toBe(1)

    localStorage.clear()
    expect(localStorage.getItem("kt.test.key")).toBeNull()
  })

  it("keeps sessionStorage independent from localStorage", () => {
    localStorage.clear()
    sessionStorage.clear()
    sessionStorage.setItem("kt.test.session", "1")
    expect(localStorage.getItem("kt.test.session")).toBeNull()
    expect(sessionStorage.getItem("kt.test.session")).toBe("1")
    sessionStorage.clear()
  })
})
