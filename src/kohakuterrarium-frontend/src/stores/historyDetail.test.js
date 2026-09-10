import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import api, { sessionAPI, terrariumAPI } from "@/utils/api"
import { useChatStore } from "./chat"
let chat
const record = {
  _history_key: "key",
  _history_truncated: true,
  _history_detail: "opaque",
  content: "preview",
  sender: "worker",
}
const metadata = {
  version: 1,
  stream: "channel",
  history_id: "h",
  before: "b",
  after: "a",
  has_older: true,
  has_newer: false,
  reset_required: false,
}
beforeEach(() => {
  setActivePinia(createPinia())
  chat = useChatStore()
  chat._instanceId = "g"
  chat._instanceGraphId = "g"
})
afterEach(() => {
  chat._cleanup()
  vi.restoreAllMocks()
})
it.each(["live", "saved"])(
  "retrieves %s full records with opaque identity parameters and no paging limit",
  async (kind) => {
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValue({ data: { record: {}, history_page: metadata } })
    const adapter = kind === "live" ? terrariumAPI : sessionAPI
    await adapter.getHistoryDetail("id", "ch:work", {
      stream: "channel",
      ref: "opaque",
      history_id: "h",
    })
    expect(get).toHaveBeenCalledWith(
      kind === "live"
        ? "/sessions/id/creatures/ch%3Awork/history/detail"
        : "/sessions/id/history/ch%3Awork/detail",
      { params: { stream: "channel", ref: "opaque", history_id: "h" } },
    )
  },
)
it("keeps saved channel detail linked across older materialization and drops stale detail responses", async () => {
  vi.spyOn(sessionAPI, "getHistoryPage")
    .mockResolvedValueOnce({ messages: [record], events: [], history_page: metadata })
    .mockResolvedValueOnce({
      messages: [{ ...record, _history_key: "older", content: "old", _history_truncated: false }],
      events: [],
      history_page: { ...metadata, before: "older", has_older: false },
    })
  let release
  const detail = vi.spyOn(sessionAPI, "getHistoryDetail").mockImplementationOnce(
    () =>
      new Promise((r) => {
        release = r
      }),
  )
  await chat.initHistoryPage("ch:work", { kind: "saved", sessionName: "saved" })
  expect(chat.messagesByTab["ch:work"][0]._historyDetails).toEqual(["key"])
  const pending = chat.loadHistoryRecord("ch:work", "key")
  await chat.prefetchOlderHistory("ch:work")
  chat.materializeOlderHistory("ch:work")
  release({
    record: { ...record, content: "full value", _history_truncated: false },
    history_page: metadata,
  })
  expect((await pending).applied).toBe(true)
  expect(chat.messagesByTab["ch:work"].map((m) => m.content)).toEqual(["old", "full value"])
  expect(chat.messagesByTab["ch:work"][1]._historyDetails).toEqual([])
  expect(detail).toHaveBeenCalledWith("saved", "ch:work", {
    stream: "channel",
    ref: "opaque",
    history_id: "h",
  })
  vi.mocked(sessionAPI.getHistoryPage).mockResolvedValueOnce({
    messages: [record],
    events: [],
    history_page: metadata,
  })
  await chat.initHistoryPage("ch:work", { kind: "saved", sessionName: "saved" })
  detail.mockImplementationOnce(
    () =>
      new Promise((r) => {
        release = r
      }),
  )
  const stale = chat.loadHistoryRecord("ch:work", "key")
  chat.resetHistoryPage("ch:work")
  release({
    record: { ...record, content: "stale full", _history_truncated: false },
    history_page: metadata,
  })
  expect((await stale).applied).toBe(false)
  expect(chat.messagesByTab["ch:work"][0].content).toBe("preview")
})
