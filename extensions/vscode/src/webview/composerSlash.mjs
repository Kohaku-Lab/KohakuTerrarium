import { computed, h } from 'vue'
import { handleSlashKeydown, SlashCommandMenu, useSlashCommandCompletion } from '@kohakuterrarium/chat-ui'

import { useDensity } from '@/composables/useDensity'

export function useComposerSlash({ chat, draft, tab }) {
  const slash = useSlashCommandCompletion({ chat, inputText: draft, activeTabKey: tab })
  // Compact controls retain the host's desktop Enter-to-send behavior.
  const { isCompact } = useDensity()
  const keyboard = (event) =>
    handleSlashKeydown(event, {
      open: slash.open.value,
      entries: slash.entries.value,
      selectedIndex: slash.selectedIndex.value,
      move: slash.move,
      choose: slash.choose,
      dismiss: slash.dismiss,
    })
  const props = computed(() => ({
    ariaAutocomplete: 'list',
    ariaExpanded: slash.open.value,
    ariaControls: 'slash-command-menu',
    ariaActivedescendant: slash.activeDescendant.value,
    inputRole: 'combobox',
    compactMode: isCompact.value,
    sendOnEnter: true,
    onKeydown: keyboard,
    onFocus: slash.reopen,
    onBlur: slash.dismiss,
    onInput: slash.releaseOwnedTarget,
  }))
  const suggestions = () =>
    h(SlashCommandMenu, {
      open: slash.open.value,
      loading: slash.loading.value,
      error: slash.error.value,
      entries: slash.entries.value,
      selectedIndex: slash.selectedIndex.value,
      onChoose: slash.choose,
      onSelectIndex: (index) => (slash.selectedIndex.value = index),
    })

  function chooseAtSubmit() {
    if (!slash.open.value || !slash.entries.value.length) return false
    slash.choose(slash.entries.value[slash.selectedIndex.value] || slash.entries.value[0])
    return true
  }

  async function send(text, assertCurrent, dispatch) {
    const key = tab.value
    let target = null
    try {
      target = await chat.prepareSlashSend({ key, creature: key, type: 'creature' }, text)
    } catch (cause) {
      console.warn('Slash inventory lookup failed; using command fallback:', cause)
    }
    assertCurrent()
    chat.markSlashTarget(key, target)
    const marker = chat._slashTargetByTab[key]
    try {
      return await dispatch()
    } finally {
      if (chat._slashTargetByTab[key] === marker) chat.markSlashTarget(key, null)
    }
  }

  return { props, suggestions, chooseAtSubmit, send }
}
