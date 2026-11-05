import { expect, it } from "vitest"
import { SpeechMouth } from "../../../../examples/packages/live2d_pet/ui/speech.js"

it("plays varied syllables with punctuation rests and drops backlogs, stops, stale or disabled speech", () => {
  let time = 0,
    sequence = 0
  const mouth = new SpeechMouth(
    () => time,
    () => time,
  )
  let work = { hostId: null, sessionId: "a", creatureId: "alice", connected: true }
  const send = (text, active = true) => {
    work = { ...work, speech: { stream: 1, sequence: ++sequence, text, active, at: time } }
    mouth.observe(work)
  }
  const tick = (ms) => {
    const values = []
    for (let i = 0; i < ms; i += 10) {
      time += 10
      values.push(mouth.sample() || 0)
    }
    return values
  }
  send("mounted during existing reply")
  expect(tick(100).every((n) => n === 0)).toBe(true)
  send("好。呀")
  const samples = tick(600)
  expect(Math.max(...samples)).toBeGreaterThan(0.4)
  expect(Math.max(...samples)).toBeLessThan(0.75)
  expect(samples.slice(32, 47).every((n) => n < 0.01)).toBe(true)
  expect(Math.max(...samples.slice(48))).toBeGreaterThan(0.3)
  send("很多文字".repeat(500))
  expect(mouth.queue.reduce((sum, beat) => sum + beat.duration, 0)).toBeLessThanOrEqual(900)
  const queued = mouth.queue.length
  mouth.observe(work)
  expect(mouth.queue).toHaveLength(queued)
  tick(70)
  send("", false)
  expect(tick(250).at(-1)).toBe(0)
  expect(mouth.sample()).toBeNull()
  send("中断前")
  tick(60)
  work = { ...work, connected: false }
  mouth.observe(work)
  expect(mouth.sample()).toBeNull()
  work = { ...work, connected: true }
  mouth.observe(work)
  expect(mouth.sample()).toBeNull()
  for (const method of ["configure", "suspend"]) {
    send("新输出")
    expect(Math.max(...tick(120))).toBeGreaterThan(0.2)
    mouth[method](method === "suspend")
    expect(mouth.sample()).toBeNull()
    send("关闭时忽略的文字")
    mouth[method](method !== "suspend")
    expect(tick(100).every((n) => n === 0)).toBe(true)
  }
  send("未收到结束事件")
  expect(tick(1400).at(-1)).toBe(0)
  time += 2000
  mouth.observe({ ...work, speech: { ...work.speech, sequence: ++sequence } })
  expect(tick(100).every((n) => n === 0)).toBe(true)
  send("仍在说")
  tick(60)
  work = { ...work, sessionId: "b" }
  mouth.observe(work)
  expect(mouth.sample()).toBeNull()
})
