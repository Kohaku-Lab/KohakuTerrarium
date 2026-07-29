import { computed, ref, watch } from "vue"

const SLASH_QUERY_RE = /^\/([^\s]*)$/

export function useSlashCommandCompletion({ chat, inputText, activeTabKey }) {
  const loading = ref(false)
  const selectedIndex = ref(0)
  const dismissed = ref(false)
  const tab = computed(() => {
    const key = activeTabKey.value
    return key ? { key, creature: key, type: key.startsWith("ch:") ? "channel" : "creature" } : null
  })
  const inventory = computed(
    () => chat.commandInventoryByTab[tab.value?.key] || { commands: [], skills: [] },
  )
  const query = computed(() => {
    if (tab.value?.type === "channel") return null
    const match = inputText.value.match(SLASH_QUERY_RE)
    return match ? match[1].toLowerCase() : null
  })
  const entries = computed(() => {
    if (query.value == null) return []
    const needle = query.value
    const commands = (inventory.value.commands || []).map((entry) => ({
      ...entry,
      type: "command",
    }))
    const commandNamespace = new Set(
      commands.flatMap((entry) => [
        entry.name.toLowerCase(),
        ...(entry.aliases || []).map((alias) => alias.toLowerCase()),
      ]),
    )
    const skills = (inventory.value.skills || [])
      .filter(
        (entry) =>
          entry.enabled &&
          !entry.invocation_blocked &&
          !commandNamespace.has(entry.name.toLowerCase()),
      )
      .map((entry) => ({ ...entry, type: "skill" }))
    const visibleCommands = commands.filter((entry) => entry.name.toLowerCase() === "goal")
    return [...visibleCommands, ...skills]
      .filter(
        (entry) =>
          entry.name.toLowerCase().includes(needle) ||
          entry.aliases?.some((alias) => alias.toLowerCase().includes(needle)),
      )
      .sort((left, right) => {
        if (left.type !== right.type) return left.type === "command" ? -1 : 1
        const leftPrefix = left.name.toLowerCase().startsWith(needle) ? 0 : 1
        const rightPrefix = right.name.toLowerCase().startsWith(needle) ? 0 : 1
        return leftPrefix - rightPrefix || left.name.localeCompare(right.name)
      })
      .map((entry, flatIndex) => ({ ...entry, flatIndex }))
  })
  const open = computed(() => query.value != null && !dismissed.value)
  const activeDescendant = computed(() =>
    open.value && entries.value.length ? `slash-option-${selectedIndex.value}` : undefined,
  )

  async function ensureLoaded({ force = false } = {}) {
    if (!tab.value || tab.value.type === "channel") return
    loading.value = true
    try {
      await chat.loadCommandInventory(tab.value, { force })
    } catch (error) {
      console.warn("Failed to load command inventory", error)
    } finally {
      loading.value = false
    }
  }

  function choose(entry) {
    if (!entry) return
    inputText.value = `/${entry.name} `
    chat.markSlashTarget(tab.value, entry)
  }

  function move(delta) {
    if (!entries.value.length) return
    selectedIndex.value =
      (selectedIndex.value + delta + entries.value.length) % entries.value.length
  }

  function clearTarget() {
    chat.markSlashTarget(tab.value, null)
  }

  function dismiss() {
    dismissed.value = true
    clearTarget()
  }

  function reopen() {
    dismissed.value = false
  }

  watch(query, (value) => {
    dismissed.value = false
    selectedIndex.value = 0
    if (value != null && activeTabKey.value) void ensureLoaded()
  })
  watch(entries, (value) => {
    if (selectedIndex.value >= value.length) selectedIndex.value = 0
  })
  watch(activeTabKey, () => {
    dismissed.value = false
    selectedIndex.value = 0
    if (query.value != null && activeTabKey.value) void ensureLoaded()
  })

  return {
    activeDescendant,
    choose,
    clearTarget,
    dismiss,
    ensureLoaded,
    entries,
    loading,
    move,
    open,
    reopen,
    selectedIndex,
  }
}
