/**
 * Shared message store.
 * Both chat tabs and inspector read from the same data.
 * Populated by WebSocket events at runtime.
 */

export const useMessagesStore = defineStore("messages", {
  state: () => ({
    /** @type {Object<string, {sender: string, content: string, timestamp: string}[]>} */
    channelMessages: {},
    /** @type {Object<string, {output: string, timestamp: string}[]>} */
    creatureOutput: {},
  }),

  actions: {
    addChannelMessage(channelName, msg) {
      if (!this.channelMessages[channelName]) {
        this.channelMessages[channelName] = []
      }
      this.channelMessages[channelName].push(msg)
    },

    addCreatureOutput(creatureName, line) {
      if (!this.creatureOutput[creatureName]) {
        this.creatureOutput[creatureName] = []
      }
      this.creatureOutput[creatureName].push(line)
    },

    getChannelMessages(channelName) {
      return this.channelMessages[channelName] || []
    },

    getCreatureOutput(creatureName) {
      return this.creatureOutput[creatureName] || []
    },

    clearForInstance() {
      this.channelMessages = {}
      this.creatureOutput = {}
    },
  },
})
