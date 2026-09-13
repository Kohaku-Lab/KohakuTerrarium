import { describe, expect, it, vi } from "vitest"

import { handleSlashKeydown } from "./slashKeyboard"

function event(key, init = {}) {
  return {
    key,
    prevented: false,
    stopped: false,
    ...init,
    preventDefault() {
      this.prevented = true
    },
    stopPropagation() {
      this.stopped = true
    },
  }
}

const context = (overrides = {}) => ({
  open: true,
  entries: [{ name: "goal" }, { name: "research" }],
  selectedIndex: 0,
  move: vi.fn(),
  choose: vi.fn(),
  dismiss: vi.fn(),
  ...overrides,
})

describe("handleSlashKeydown", () => {
  it("does nothing while the menu is closed", () => {
    const ctx = context({ open: false })
    const e = event("ArrowDown")
    expect(handleSlashKeydown(e, ctx)).toBe(false)
    expect(e.prevented).toBe(false)
    expect(ctx.move).not.toHaveBeenCalled()
  })

  it("moves the selection on ArrowDown/ArrowUp and consumes the event", () => {
    const ctx = context()
    const down = event("ArrowDown")
    expect(handleSlashKeydown(down, ctx)).toBe(true)
    expect(down.prevented).toBe(true)
    expect(ctx.move).toHaveBeenLastCalledWith(1)
    handleSlashKeydown(event("ArrowUp"), ctx)
    expect(ctx.move).toHaveBeenLastCalledWith(-1)
  })

  it("completes the highlighted entry on Enter/Tab without dispatching", () => {
    const ctx = context({ selectedIndex: 1 })
    for (const key of ["Enter", "Tab"]) {
      const e = event(key)
      expect(handleSlashKeydown(e, ctx)).toBe(true)
      expect(e.prevented).toBe(true)
    }
    expect(ctx.choose).toHaveBeenCalledTimes(2)
    expect(ctx.choose).toHaveBeenLastCalledWith({ name: "research" })
  })

  it("leaves Enter/Tab alone when no entry is selectable so the composer can submit", () => {
    const ctx = context({ entries: [] })
    const e = event("Enter")
    expect(handleSlashKeydown(e, ctx)).toBe(false)
    expect(e.prevented).toBe(false)
    expect(ctx.choose).not.toHaveBeenCalled()
  })

  it("dismisses on Escape without bubbling", () => {
    const ctx = context()
    const e = event("Escape")
    expect(handleSlashKeydown(e, ctx)).toBe(true)
    expect(e.prevented).toBe(true)
    expect(e.stopped).toBe(true)
    expect(ctx.dismiss).toHaveBeenCalledTimes(1)
  })

  it("consumes IME composition keys without choosing, moving or dismissing", () => {
    const ctx = context()
    for (const e of [
      event("Enter", { isComposing: true }),
      event("ArrowDown", { isComposing: true }),
      event("Escape", { isComposing: true }),
      event("Enter", { keyCode: 229 }),
      event("ArrowUp", { keyCode: 229 }),
    ]) {
      expect(handleSlashKeydown(e, ctx)).toBe(true)
      expect(e.prevented).toBe(false)
    }
    expect(ctx.choose).not.toHaveBeenCalled()
    expect(ctx.move).not.toHaveBeenCalled()
    expect(ctx.dismiss).not.toHaveBeenCalled()
  })

  it("consumes an already-prevented event without touching the menu", () => {
    const ctx = context()
    const e = event("Enter", { defaultPrevented: true })
    expect(handleSlashKeydown(e, ctx)).toBe(true)
    expect(e.prevented).toBe(false)
    expect(ctx.choose).not.toHaveBeenCalled()
    expect(ctx.move).not.toHaveBeenCalled()
    expect(ctx.dismiss).not.toHaveBeenCalled()
  })
})
