import { defineExtension, useMessageBlock } from "@kohakuterrarium/ui-sdk"

function parsePoll(text) {
  try {
    const poll = JSON.parse(text)
    const options = Array.isArray(poll.options) ? poll.options.map(String) : []
    if (typeof poll.question !== "string" || !options.length) return null
    return { question: poll.question, options }
  } catch (_err) {
    return null
  }
}

const Poll = {
  setup() {
    const block = useMessageBlock()
    return {
      block,
      poll: () => parsePoll(block.source.value),
      pick: (option) => block.say(option),
    }
  },
  template: `
    <div v-if="poll()" style="display:flex;flex-direction:column;gap:6px">
      <strong>{{ poll().question }}</strong>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        <button v-for="option in poll().options" :key="option" @click="pick(option)">{{ option }}</button>
      </div>
    </div>
    <pre v-else style="white-space:pre-wrap">{{ block.source.value }}</pre>
  `,
}

export default defineExtension((kt) => {
  kt.registerMessageRenderer("poll", Poll)
})
