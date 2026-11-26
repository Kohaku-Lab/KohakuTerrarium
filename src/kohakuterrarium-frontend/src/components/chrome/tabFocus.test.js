import { afterEach, describe, expect, it } from "vitest"

import { moveTabFocus } from "./tabFocus"

function tablist(labels, disabled = []) {
  const list = document.createElement("div")
  list.setAttribute("role", "tablist")
  for (const label of labels) {
    const tab = document.createElement("button")
    tab.setAttribute("role", "tab")
    tab.textContent = label
    tab.disabled = disabled.includes(label)
    list.append(tab)
  }
  list.addEventListener("keydown", moveTabFocus)
  document.body.append(list)
  return list
}

function press(list, label, key) {
  const tab = Array.from(list.children).find((el) => el.textContent === label)
  tab.focus()
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })
  tab.dispatchEvent(event)
  return { focused: document.activeElement.textContent, prevented: event.defaultPrevented }
}

describe("moveTabFocus", () => {
  afterEach(() => {
    document.body.innerHTML = ""
  })

  it("moves focus with arrows in both orientations, wrapping at the ends", () => {
    const list = tablist(["a", "b", "c"])
    expect(press(list, "a", "ArrowRight")).toEqual({ focused: "b", prevented: true })
    expect(press(list, "b", "ArrowDown")).toEqual({ focused: "c", prevented: true })
    expect(press(list, "c", "ArrowDown")).toEqual({ focused: "a", prevented: true })
    expect(press(list, "a", "ArrowUp")).toEqual({ focused: "c", prevented: true })
    expect(press(list, "c", "ArrowLeft")).toEqual({ focused: "b", prevented: true })
  })

  it("jumps with Home and End and skips disabled tabs", () => {
    const list = tablist(["a", "b", "c", "d"], ["d"])
    expect(press(list, "b", "End")).toEqual({ focused: "c", prevented: true })
    expect(press(list, "c", "Home")).toEqual({ focused: "a", prevented: true })
    expect(press(list, "c", "ArrowRight")).toEqual({ focused: "a", prevented: true })
  })

  it("leaves other keys and non-tab targets alone", () => {
    const list = tablist(["a", "b"])
    expect(press(list, "a", "Enter")).toEqual({ focused: "a", prevented: false })
    const outsider = document.createElement("span")
    list.append(outsider)
    const event = new KeyboardEvent("keydown", {
      key: "ArrowRight",
      bubbles: true,
      cancelable: true,
    })
    outsider.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })
})
