/**
 * Shared message store — channel posts and per-creature output rings.
 *
 * Scoped per macro-shell attach target (creature_id /
 * terrarium_id) so two attach tabs for two creatures of the same
 * config don't share a single message map. ``AttachTab`` provides
 * ``kt:scope`` once near the top of its setup; descendants that
 * call :func:`useMessagesStore` automatically pick the per-attach
 * store via inject. Outside an AttachTab (v1 page-routed flow) the
 * default singleton is returned, preserving v1 behaviour.
 */

import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"

const _options = {
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
}

const _factories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _factories.get(key)
  if (!useFn) {
    useFn = defineStore(`messages:${key}`, _options)
    _factories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _factories.delete(key)
      })
    }
  }
  return useFn
}

export function useMessagesStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
