import { h } from 'vue'

const ICON_PATHS = {
  add: 'M17 15V8h-2v7H8v2h7v7h2v-7h7v-2z',
  chevron: 'M16 22 6 12l1.4-1.4 8.6 8.6 8.6-8.6L26 12z',
  refresh: 'M12 10H6.78A11 11 0 0 1 27 16h2A13 13 0 0 0 6 7.68V4H4v8h8Zm8 12h5.22A11 11 0 0 1 5 16H3a13 13 0 0 0 23 8.32V28h2v-8h-8Z',
  send: 'm27.45 15.11-22-11a1 1 0 0 0-1.08.12 1 1 0 0 0-.33 1L7 16 4 26.74A1 1 0 0 0 5 28a1 1 0 0 0 .45-.11l22-11a1 1 0 0 0 0-1.78m-20.9 10L8.76 17H18v-2H8.76L6.55 6.89 24.76 16Z',
  stop: 'M16 2a14 14 0 1 0 14 14A14 14 0 0 0 16 2m6 18a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2Z',
}

export function createViewRenderers({
  ConversationMessage,
  MarkdownRenderer,
  ArtifactScope,
  available,
  busy,
  currentSession,
  openSession,
  resumeSession,
}) {
  function renderSharedText(content, breaks = false) {
    return h(MarkdownRenderer, { content, breaks })
  }

  function renderTranscriptMessage(message, { reply }) {
    const render = () =>
      h(ConversationMessage, {
        message,
        renderText: renderSharedText,
        onReply: ({ actionId, values }) => reply(actionId, values),
      })
    return ArtifactScope ? h(ArtifactScope, null, { default: render }) : render()
  }

  function icon(name) {
    return h(
      'svg',
      {
        class: `action-icon action-icon--${name}`,
        viewBox: '0 0 32 32',
        'aria-hidden': 'true',
      },
      [h('path', { d: ICON_PATHS[name] })],
    )
  }

  function actionButton(label, iconName, { text, ...options } = {}) {
    return h('button', { type: 'button', title: label, 'aria-label': label, ...options }, [icon(iconName), text ? h('span', text) : null])
  }

  function renderSession(session) {
    const selected = currentSession.value?.session?.runtimeId === session.runtimeId
    const common = {
      disabled: busy.value || !available.value,
      class: ['session-row', selected ? 'is-active' : ''],
    }
    if (!session.isLive) {
      return h(
        'button',
        {
          ...common,
          key: session.conversationId || session.savedName,
          disabled: common.disabled || !session.savedName,
          'aria-label': `Resume Session ${session.title}`,
          onClick: () => resumeSession(session),
        },
        [
          h('span', {
            class: 'status-dot status-dot--dormant',
            'aria-hidden': 'true',
          }),
          h('span', { class: 'row-label' }, session.title),
        ],
      )
    }
    if (session.creatures.length === 1) {
      const creature = session.creatures[0]
      return h(
        'button',
        {
          ...common,
          key: session.conversationId || session.runtimeId,
          'aria-label': `Open Session ${session.title}`,
          onClick: () => openSession(session, creature.id),
        },
        [h('span', { class: 'status-dot', 'aria-hidden': 'true' }), h('span', { class: 'row-label' }, session.title)],
      )
    }
    return h(
      'div',
      {
        class: 'session-group',
        key: session.conversationId || session.runtimeId,
      },
      [
        h(
          'div',
          {
            class: ['session-row', 'session-row--label', selected ? 'is-active' : ''],
          },
          [h('span', { class: 'status-dot', 'aria-hidden': 'true' }), h('span', { class: 'row-label' }, session.title)],
        ),
        ...session.creatures.map((creature) =>
          h(
            'button',
            {
              class: ['session-row', 'creature-row', currentSession.value?.targetCreatureId === creature.id ? 'is-active' : ''],
              disabled: common.disabled,
              'aria-label': `Open Creature ${creature.name} in Session ${session.title}`,
              onClick: () => openSession(session, creature.id),
            },
            [h('span', { class: 'row-label' }, creature.name)],
          ),
        ),
      ],
    )
  }

  return {
    actionButton,
    icon,
    renderSession,
    renderSharedText,
    renderTranscriptMessage,
  }
}
