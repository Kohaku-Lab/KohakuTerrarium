import { onUnmounted, shallowRef, watch } from "vue"

/** Opt-in, ephemeral output: no transcript scan, replay, or additional connection. */
export function useWorkSpeech(binding) {
  const speech = shallowRef(null)
  let generation = 0
  const stop = watch(
    () => {
      const chat = binding.value
      return [chat, chat?.terrariumTarget, chat?.wsStatus, chat?.viewingRunningBranch]
    },
    ([chat, source, connection], _, cleanup) => {
      const stream = ++generation
      let sequence = 0
      const publish = (text = "", active = false) => {
        speech.value = Object.freeze({ stream, sequence: ++sequence, text, active, at: Date.now() })
      }
      publish()
      if (!chat || !source || connection !== "open") return
      const off = chat.$onAction(({ name, args, after }) => {
        if (name !== "_onMessage") return
        const event = args[0]
        after(() => {
          if (
            generation !== stream ||
            event?.history ||
            event?.replay ||
            chat._tabForSource(event?.source || "") !== source ||
            !chat._frameMatchesViewedBranch(source, event)
          )
            return
          if (event.type === "text" && typeof event.content === "string" && event.content) {
            publish(event.content.slice(-256), true)
          } else if (
            [
              "processing_start",
              "processing_end",
              "processing_error",
              "idle",
              "ask_text",
              "confirm",
              "selection",
              "card",
            ].includes(event.type) ||
            (event.type === "activity" &&
              ["tool_start", "subagent_start", "assistant_reasoning", "processing_error"].includes(
                event.activity_type,
              ))
          )
            publish()
        })
      }, true)
      cleanup(off)
    },
    { immediate: true, flush: "sync" },
  )
  onUnmounted(stop)
  return speech
}
