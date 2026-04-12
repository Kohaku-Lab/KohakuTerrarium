import { useMessagesStore } from "@/stores/messages"

export const useInspectorStore = defineStore("inspector", {
  state: () => ({
    /** @type {"creature" | "channel" | "overview" | null} */
    type: null,
    /** @type {string | null} */
    selectedName: null,
    /** @type {object | null} */
    data: null,
    /** @type {string | null} */
    _instanceId: null,
  }),

  getters: {
    channelMessages: (state) => {
      if (state.type !== "channel" || !state.selectedName) return []
      const messages = useMessagesStore()
      return messages.getChannelMessages(state.selectedName)
    },

    creatureOutputLines: (state) => {
      if (state.type !== "creature" || !state.selectedName) return []
      const messages = useMessagesStore()
      return messages.getCreatureOutput(state.selectedName)
    },
  },

  actions: {
    selectCreature(creature) {
      this.type = "creature"
      this.selectedName = creature.name
      this.data = { ...creature }
    },

    selectChannel(channel) {
      this.type = "channel"
      this.selectedName = channel.name
      this.data = { ...channel }
    },

    /** Show overview. Called on initial load (skips if same instance) and on close button (force=true). */
    showOverview(instance, force) {
      if (!force && this._instanceId === instance.id && this.type === "overview") return
      this._instanceId = instance.id
      this.type = "overview"
      this.selectedName = instance.config_name
      this.data = instance
    },

    clear() {
      this.type = null
      this.selectedName = null
      this.data = null
    },
  },
})
