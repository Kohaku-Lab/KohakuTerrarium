import { afterEach, expect, it, vi } from "vitest"
import { PetBehavior } from "../../../../examples/packages/live2d_pet/ui/behavior.js"
import { PetInteraction } from "../../../../examples/packages/live2d_pet/ui/interaction.js"
import { connectWork } from "../../../../examples/packages/live2d_pet/ui/work.js"
import { AgentActions } from "../../../../examples/packages/live2d_pet/ui/agent-actions.js"
import sets from "../../../../examples/packages/live2d_pet/ui/action-sets.json"
import { SpeechMouth } from "../../../../examples/packages/live2d_pet/ui/speech.js"

afterEach(() => {
  vi.restoreAllMocks()
  history.replaceState(null, "", "/")
  document.body.replaceChildren()
})

it("accepts only ordered work from the bound parent, reports disconnects, and releases its listener", () => {
  history.replaceState(null, "", "/stage.html?channel=current")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  document.body.innerHTML = '<div id="work-status"></div>'
  const behavior = new PetBehavior()
  const activity = vi.spyOn(PetInteraction.prototype, "activity")
  const interaction = Object.create(PetInteraction.prototype)
  interaction.now = () => 100
  interaction.autoSleeping = false
  const speech = new SpeechMouth()
  const disconnect = connectWork(behavior, interaction, "dragon", { debug: true, speech })
  const work = {
    sessionId: "a",
    hostId: null,
    label: "Agent",
    connected: true,
    status: "running",
    busy: true,
    started: 1,
  }
  const send = (data, origin = "http://localhost", source = window) =>
    window.dispatchEvent(
      new MessageEvent("message", {
        origin,
        source,
        data: { type: "kt-live2d-work", channel: "current", sequence: 1, work, ...data },
      }),
    )
  try {
    send({}, "https://other.test")
    send({}, "http://localhost", null)
    send({ channel: "old" })
    send({ sequence: 0 })
    expect(behavior.work).toBeNull()
    send({})
    expect(behavior.working).toBe(true)
    expect(document.body.textContent).toContain("Agent · 正在忙")
    expect(activity).toHaveBeenCalledTimes(1)
    send({ work: null })
    expect(behavior.working).toBe(true)
    send({ sequence: 2, work: { ...work, status: "waiting", busy: false } })
    expect(document.body.textContent).toContain("等你确认")
    expect(activity).toHaveBeenCalledTimes(2)
    send({ sequence: 3, work: { ...work, status: "ready", connected: false } })
    expect(behavior.reaction).toBeNull()
    expect(document.body.textContent).toContain("状态暂不可用")
    send({ sequence: 4, work: null })
    expect(document.body.textContent).toContain("未绑定会话")
    disconnect.preview("waiting")
    expect(behavior.action().state).toBe("waiting")
    expect(document.body.textContent).toContain("动作试演")
    send({ sequence: 5, work: { ...work, status: "ready" } })
    expect(behavior.action().state).toBe("waiting")
    disconnect.preview("ready")
    expect(behavior.action().state).toBe("waiting")
    expect(behavior.completedAt).toBeGreaterThan(performance.now())
    disconnect.preview("live")
    expect(behavior.work.status).toBe("ready")
    expect(behavior.action().state).toBe("idle")
    expect(document.body.textContent).not.toContain("动作试演")
    disconnect.preview("waiting")
    disconnect.configure({ debug: false, followWork: true })
    expect(behavior.action().state).toBe("idle")
    disconnect.preview("waiting")
    expect(behavior.action().state).toBe("idle")
    disconnect.configure({ debug: false, followWork: false })
    send({ sequence: 6, work: { ...work, status: "running" } })
    expect(behavior.work).toBeNull()
    send({
      sequence: 6.1,
      work: { ...work, speech: { stream: 1, sequence: 1, active: true, text: "untrusted" } },
    })
    expect(speech.queue).toHaveLength(0)
    const actor = new AgentActions(behavior, "dragon", sets.sets.dragon)
    actor.configure(true)
    expect(
      actor.receive({
        operation: "perform",
        model: "dragon",
        action: "nod",
        id: "gesture",
        expiresAt: Date.now() + 2000,
      }),
    ).toBe(true)
    send({ sequence: 7, work: { ...work, status: "waiting" } })
    expect(behavior.work).toBeNull()
    expect(actor.current(performance.now())).toBe(behavior)
    expect(
      actor.receive({
        operation: "perform",
        model: "dragon",
        action: "nod",
        id: "blocked",
        expiresAt: Date.now() + 2000,
      }),
    ).toBe(false)
    send({ sequence: 8, work: { ...work, status: "running" } })
    disconnect.configure({ debug: false, followWork: true })
    expect(behavior.action().state).toBe("running")
    expect(behavior.reaction).toBeNull()
    send({ sequence: 9, work: null })
    send({
      sequence: 10,
      work: {
        ...work,
        speech: { stream: 1, sequence: 1, active: false, text: "", at: Date.now() },
      },
    })
    disconnect.configure({ followWork: false })
    const activityBeforeSpeech = activity.mock.calls.length
    send({
      sequence: 11,
      work: {
        ...work,
        speech: { stream: 1, sequence: 2, active: true, text: "自然说话。", at: Date.now() },
      },
    })
    expect(behavior.work).toBeNull()
    expect(speech.queue.length).toBeGreaterThan(0)
    expect(activity.mock.calls.length).toBe(activityBeforeSpeech + 1)
    disconnect.configure({ followWork: false, talkingMouth: false })
    expect(speech.queue).toHaveLength(0)
    disconnect()
    send({ sequence: 12 })
    expect(behavior.work).toBeNull()
  } finally {
    disconnect()
  }
})
