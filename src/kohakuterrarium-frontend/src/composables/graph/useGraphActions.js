/**
 * Graph-view mutations: connect, create, delete and lifecycle operations,
 * each mapped onto the existing session / topology / wiring routes. Every
 * call refreshes the live snapshot and reports failures as toasts.
 */

import { useGraphLiveStore } from "@/stores/graph/live"
import { useNotificationsStore } from "@/stores/notifications"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI, terrariumAPI, wiringAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * Decide what a drag from `source` (handle `sourceHandle`) onto `target`
 * means. Returns an intent string or null when the drop is not allowed.
 */
export function connectionIntent(source, sourceHandle, target) {
  if (!source || !target || source.id === target.id) return null
  if (source.kind === "creature" && target.kind === "channel" && sourceHandle === "send")
    return "send"
  if (source.kind === "channel" && target.kind === "creature" && sourceHandle === "out")
    return "listen"
  if (source.kind === "creature" && target.kind === "creature" && sourceHandle === "wire")
    return "wire"
  if (source.kind === "creature" && target.kind === "creature" && sourceHandle === "send")
    return "bridge"
  return null
}

function errorText(err) {
  return err?.response?.data?.detail || err?.message || String(err)
}

export function useGraphActions(view) {
  const live = useGraphLiveStore()
  const notify = useNotificationsStore()
  const tabs = useTabsStore()
  const { t } = useI18n()

  async function run(label, fn) {
    if (view.isSample) {
      notify.push({ level: "info", title: t("graph.toast.sampleReadOnly") })
      return null
    }
    try {
      const result = await fn()
      await live.refresh()
      return result ?? true
    } catch (err) {
      notify.push({ level: "error", title: label, body: errorText(err) })
      return null
    }
  }

  async function ensureSameSession(channel, creature) {
    if (channel.sessionId === creature.sessionId) return channel.sessionId
    const merged = await terrariumAPI.mergeGraphs(
      channel.sessionId,
      creature.sessionId,
      channel.name,
    )
    return merged?.session_id || channel.sessionId
  }

  function connect(intent, source, target) {
    switch (intent) {
      case "send":
        return run(t("graph.toast.connectFailed"), async () => {
          const sid = await ensureSameSession(target.channel, source.creature)
          return terrariumAPI.wireCreature(sid, source.creature.id, target.channel.name, "send")
        })
      case "listen":
        return run(t("graph.toast.connectFailed"), async () => {
          const sid = await ensureSameSession(source.channel, target.creature)
          return terrariumAPI.wireCreature(sid, target.creature.id, source.channel.name, "listen")
        })
      case "wire":
        return run(t("graph.toast.connectFailed"), () =>
          wiringAPI.addOutput(source.creature.sessionId, source.creature.id, {
            to: target.creature.id,
            with_content: true,
            prompt_format: "simple",
            allow_self_trigger: false,
          }),
        )
      case "bridge":
        return run(t("graph.toast.connectFailed"), () =>
          terrariumAPI.connect(
            source.creature.sessionId,
            source.creature.id,
            target.creature.id,
            null,
            "broadcast",
          ),
        )
      default:
        return Promise.resolve(null)
    }
  }

  function setChannelMembership(creature, channelName, direction, enabled) {
    return run(t("graph.toast.connectFailed"), () =>
      enabled
        ? terrariumAPI.wireCreature(creature.sessionId, creature.id, channelName, direction)
        : terrariumAPI.unwireCreature(creature.sessionId, creature.id, channelName, direction),
    )
  }

  /** Move a creature's membership on one channel from `from` to `to` (none | listen | send | both). */
  function setMembership(creature, channelName, from, to) {
    const has = (mode, dir) => mode === "both" || mode === dir
    return run(t("graph.toast.connectFailed"), async () => {
      for (const dir of ["listen", "send"]) {
        const before = has(from, dir)
        const after = has(to, dir)
        if (before === after) continue
        if (after)
          await terrariumAPI.wireCreature(creature.sessionId, creature.id, channelName, dir)
        else await terrariumAPI.unwireCreature(creature.sessionId, creature.id, channelName, dir)
      }
      return true
    })
  }

  function removeEdge(edge) {
    return run(t("graph.toast.removeFailed"), async () => {
      if (edge.kind === "wire")
        return wiringAPI.removeOutput(edge.sessionId, edge.source, edge.edgeId)
      if (edge.kind === "channel") {
        const directions = edge.mode === "both" ? ["send", "listen"] : [edge.mode]
        for (const d of directions)
          await terrariumAPI.unwireCreature(edge.sessionId, edge.source, edge.channelName, d)
      }
      return true
    })
  }

  function updateWire(edge, { withContent, prompt }) {
    return run(t("graph.toast.updateFailed"), async () => {
      const target = view.model.creatures.find((c) => c.id === edge.target)
      await wiringAPI.removeOutput(edge.sessionId, edge.source, edge.edgeId)
      return wiringAPI.addOutput(edge.sessionId, edge.source, {
        to: target?.id || edge.target,
        with_content: withContent,
        prompt: prompt || null,
        prompt_format: "simple",
        allow_self_trigger: false,
      })
    })
  }

  /** Turn a wire around: the target now sends its turn output to the source, same payload and prompt. */
  function reverseWire(edge) {
    return run(t("graph.toast.updateFailed"), async () => {
      await wiringAPI.removeOutput(edge.sessionId, edge.source, edge.edgeId)
      return wiringAPI.addOutput(edge.sessionId, edge.target, {
        to: edge.source,
        with_content: edge.withContent !== false,
        prompt: edge.prompt || null,
        prompt_format: "simple",
        allow_self_trigger: false,
      })
    })
  }

  const removeCreature = (c) =>
    run(t("graph.toast.removeFailed"), () => sessionAPI.removeCreature(c.sessionId, c.id))
  const removeChannel = (ch) =>
    run(t("graph.toast.removeFailed"), () => terrariumAPI.removeChannel(ch.sessionId, ch.name))
  const interrupt = (c) =>
    run(t("graph.toast.actionFailed"), () => terrariumAPI.interruptCreature(c.sessionId, c.id))
  const start = (c) =>
    run(t("graph.toast.actionFailed"), () => terrariumAPI.startCreature(c.sessionId, c.id))
  const stop = (c) =>
    run(t("graph.toast.actionFailed"), () => terrariumAPI.stopCreature(c.sessionId, c.id))
  const stopSession = (sid) => run(t("graph.toast.actionFailed"), () => sessionAPI.stopActive(sid))

  function addChannel(sessionId, name, description = "") {
    return run(t("graph.toast.createFailed"), () =>
      terrariumAPI.addChannel(sessionId, name, "broadcast", description),
    )
  }

  /** Add a creature, optionally wired from a context (a channel or an upstream creature). */
  function addCreature(
    sessionId,
    { name, configPath, listenChannels = [], sendChannels = [], wireFrom = null },
  ) {
    return run(t("graph.toast.createFailed"), async () => {
      const created = await sessionAPI.addCreature(sessionId, {
        name,
        configPath,
        listenChannels,
        sendChannels,
      })
      if (wireFrom && created?.creature_id) {
        await wiringAPI.addOutput(sessionId, wireFrom, {
          to: created.creature_id,
          with_content: true,
          prompt_format: "simple",
          allow_self_trigger: false,
        })
      }
      return created
    })
  }

  /** Create a channel and wire `creature` as its sender in one step. */
  function addChannelFrom(creature, name) {
    return run(t("graph.toast.createFailed"), async () => {
      await terrariumAPI.addChannel(creature.sessionId, name, "broadcast", "")
      return terrariumAPI.wireCreature(creature.sessionId, creature.id, name, "send")
    })
  }

  function postToChannel(channel, content) {
    return run(t("graph.toast.sendFailed"), () =>
      terrariumAPI.sendToChannel(channel.sessionId, channel.name, content),
    )
  }

  function openChatTab(sessionId, innerTab = null) {
    return tabs.openSurface(sessionId, "chat", innerTab ? { initialTab: innerTab } : {})
  }

  function openInspector(sessionId) {
    return tabs.openSurface(sessionId, "inspector")
  }

  return {
    connect,
    setChannelMembership,
    setMembership,
    removeEdge,
    updateWire,
    reverseWire,
    removeCreature,
    removeChannel,
    interrupt,
    start,
    stop,
    stopSession,
    addChannel,
    addCreature,
    addChannelFrom,
    postToChannel,
    openChatTab,
    openInspector,
  }
}
