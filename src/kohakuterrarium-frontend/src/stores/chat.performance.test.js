import { createPinia, setActivePinia } from "pinia"
import { isReactive } from "vue"
import { beforeEach, describe, expect, it } from "vitest"

import { useChatStore } from "./chat.js"

function longHistory(count = 2000) {
  return Array.from({ length: count }, (_, index) => ({
    type: index % 2 === 0 ? "user_input" : "processing_end",
    content: `event ${index}`,
    event_id: index + 1,
    turn_index: Math.floor(index / 2) + 1,
    branch_id: 1,
    parent_branch_path: [],
  }))
}

describe("chat store long-session raw caches", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it("keeps cached history events out of Vue deep reactivity", () => {
    const chat = useChatStore("performance")

    chat._setEvents("main", longHistory())
    const events = chat.eventsByTab.main

    expect(isReactive(events)).toBe(false)
    expect(isReactive(events[0])).toBe(false)
  })

  it("does not inspect cached history when appending regular user input", () => {
    const chat = useChatStore("performance")
    chat.messagesByTab = { main: [] }
    const events = new Proxy([], {
      get(target, key, receiver) {
        if (key === Symbol.iterator) throw new Error("history scanned")
        return Reflect.get(target, key, receiver)
      },
    })
    chat._setEvents("main", events)

    expect(() =>
      chat._addMsg("main", {
        id: "u_1",
        eventId: "c_1",
        role: "user",
        content: "continue",
      }),
    ).not.toThrow()
    expect(chat.messagesByTab.main).toHaveLength(1)
  })
})
