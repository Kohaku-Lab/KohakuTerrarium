const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontend = path.resolve(root, '..', '..', 'src', 'kohakuterrarium-frontend', 'src')

function read(file) {
  return fs.readFileSync(file, 'utf8')
}

test('Dashboard and VS Code import one production conversation message component', () => {
  const shared = path.join(frontend, 'components', 'chat', 'shared', 'ConversationMessage.js')
  const dashboard = read(path.join(frontend, 'components', 'chat', 'ChatMessage.vue'))
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.equal(fs.existsSync(shared), true)
  assert.match(dashboard, /from ["']@kohakuterrarium\/chat-ui["']/)
  assert.match(webview, /from ["']@kohakuterrarium\/chat-ui["']/)
  assert.doesNotMatch(webview, /function renderMessage\(/)
  assert.doesNotMatch(webview, /function renderPart\(/)
  assert.doesNotMatch(webview, /function renderInteractive\(/)
})

test('VS Code conversation text uses the public shared Markdown renderer', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))
  const renderers = read(path.join(root, 'src', 'webview', 'viewRenderers.mjs'))

  assert.match(webview, /import\s*\{[^}]*\bMarkdownRenderer\b[^}]*\}\s*from ['"]@kohakuterrarium\/chat-ui['"]/s)
  assert.match(webview, /createViewRenderers\(\{[\s\S]*MarkdownRenderer/)
  assert.match(
    renderers,
    /function renderSharedText\(content, breaks = false\)\s*\{\s*return h\(MarkdownRenderer, \{ content, breaks \}\)\s*\}/,
  )
  const messages = [...renderers.matchAll(/h\(ConversationMessage,\s*\{([\s\S]*?)\n\s*\}\)/g)]
  assert.ok(messages.length > 0)
  for (const message of messages) assert.match(message[1], /renderText:\s*renderSharedText/)
})

test('Dashboard and VS Code consume one host-neutral transcript section', () => {
  const shared = path.join(frontend, 'components', 'chat', 'shared', 'ChatTranscriptSection.js')
  const dashboard = read(path.join(frontend, 'components', 'chat', 'ChatPanel.vue'))
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.equal(fs.existsSync(shared), true)
  assert.match(dashboard, /from ["']@kohakuterrarium\/chat-ui["']/)
  assert.match(webview, /from ["']@kohakuterrarium\/chat-ui["']/)
  assert.doesNotMatch(webview, /messages\.value\.map/)
  assert.match(webview, /processing:\s*chat\.processingByTab\[tab\.value\]/)
  assert.match(webview, /processingLabel:\s*['"][^'"]+['"]/)
  assert.doesNotMatch(webview, /`\$\{error\.value\} \$\{chat\.wsStatus\}`/)
})

test('VS Code uses the exact public shared composer without a private input or separate turn stop', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.match(webview, /import\s*\{[^}]*\bChatComposer\b[^}]*\}\s*from ['"]@kohakuterrarium\/chat-ui['"]/s)
  assert.match(webview, /h\(\s*ChatComposer,\s*\{/)
  assert.doesNotMatch(webview, /h\(['"]input['"],\s*\{[^}]*aria-label:\s*['"]Message['"]/s)
  assert.doesNotMatch(webview, /Stop Turn|stop-turn/)
  assert.match(webview, /processing:\s*!!chat\.processingByTab\[tab\.value\]/)
  assert.match(webview, /onInterrupt:\s*\(\)\s*=>\s*chat\.interrupt\(tab\.value\)/)
  assert.match(webview, /showContextActions:\s*true/)
  assert.match(webview, /onCompact:.*context\.compact/)
  assert.match(webview, /onClear:.*context\.clear/)
})

test('VS Code composer context controls render accessible inline Carbon icons', async () => {
  const iconModule = path.join(root, 'src', 'webview', 'carbonIcons.mjs')
  assert.equal(fs.existsSync(iconModule), true)

  const { renderCarbonIcon } = await import(pathToFileURL(iconModule))
  for (const name of ['collapse-all', 'clean']) {
    const icon = renderCarbonIcon(name)
    assert.equal(icon.type, 'svg')
    assert.equal(icon.props['aria-hidden'], 'true')
    assert.equal(icon.props.focusable, 'false')
    assert.equal(icon.props.fill, 'currentColor')
    assert.equal(icon.props.width, '1em')
    assert.equal(icon.props.height, '1em')
    assert.ok(icon.children.length > 0)
    assert.ok(icon.children.every((child) => child.type === 'path' && child.props.d))
  }

  const webview = read(path.join(root, 'src', 'webview', 'index.js'))
  assert.match(webview, /'compact-icon': \(\) => renderCarbonIcon\('collapse-all'\)/)
  assert.match(webview, /'clear-icon': \(\) => renderCarbonIcon\('clean'\)/)
  assert.doesNotMatch(webview, /i-carbon-(?:collapse-all|clean)/)
})

test('VS Code composer uses managed attachment conversion and preserves state until send accepts', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.match(webview, /\bbuildMessageParts\b/)
  assert.match(webview, /managedSubmit:\s*true/)
  assert.match(webview, /attachments:\s*attachments\.value/)
  assert.match(webview, /onUpdate:attachments/)
  assert.match(webview, /maxAttachmentBytes:\s*10 \* 1024 \* 1024/)
  assert.match(webview, /maxImageBytes:\s*5 \* 1024 \* 1024/)
  assert.match(webview, /createHostAcceptedChat\(\{ BridgeWebSocket, chat \}\)/)
  assert.match(webview, /return hostAcceptedChat\.send\(content\)/)
  assert.match(webview, /hostAcceptedChat[\s\S]*\.submitUIReply\(tab\.value, message\.eventId/)
  assert.doesNotMatch(webview, /outcome\.(?:accepted|queued)/)
  assert.match(webview, /if \(conversationOwnership\.isCurrent\(submittedOwner\)\)[\s\S]*scroll\.forceFollow\(\)/)
  assert.match(webview, /disabled:[\s\S]*chat\.wsStatus !== ['"]open['"]/)
  assert.match(webview, /function submitReply[\s\S]*chat\.wsStatus !== ['"]open['"][\s\S]*error\.value/)
})

test('VS Code binds transcript viewport callbacks to the rendered conversation identity', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.match(webview, /createConversationScrollController/)
  assert.match(webview, /createTranscriptBindings\(\{[\s\S]*scroll\.onViewportReady\(viewport, identity\)/)
  assert.match(webview, /createTranscriptBindings\(\{[\s\S]*scroll\.onScroll\(event, identity\)/)
  assert.match(webview, /const transcriptCallbacks = computed\(\(\) => transcriptBindings\.forIdentity\(scrollIdentity\.value\)\)/)
  assert.match(webview, /if \(conversationOwnership\.isCurrent\(submittedOwner\)\)[\s\S]*scroll\.forceFollow\(\)/)
  assert.match(webview, /const messageTail = computed\(\(\) => createMessageTailSignature\(messages\.value\)\)/)
  assert.match(webview, /const messageStructure = computed\(\(\) => messageSequenceKey\(messageSequence\.value\)\)/)
})

test('VS Code windows transcript rendering and separates structural from tail observation', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.match(webview, /createTranscriptWindow\(\)/)
  assert.match(webview, /const messageTail = computed\(\(\) => createMessageTailSignature\(messages\.value\)\)/)
  assert.match(webview, /const messageStructure = computed\(\(\) => messageSequenceKey\(messageSequence\.value\)\)/)
  assert.equal((webview.match(/createMessageSequence\(messages\.value\)/g) || []).length, 1)
  assert.match(webview, /const messageSequence = computed\(\(\) => createMessageSequence\(messages\.value\)\)/)
  assert.match(webview, /watch\(\s*\(\) => \(\{\s*identity: scrollIdentity\.value,\s*sequence: messageSequence\.value,\s*\}\)/s)
  assert.match(
    webview,
    /watch\(\s*\(\) => \(\{\s*identity: scrollIdentity\.value,\s*structure: messageStructure\.value,\s*tail: messageTail\.value,\s*\}\)/s,
  )
  assert.match(webview, /const transcriptRevision = ref\(0\)/)
  assert.match(webview, /const transcriptView = computed\(\(\) => \{[\s\S]*transcriptRevision\.value[\s\S]*transcriptWindow\.view/s)
  assert.match(webview, /transcriptRevision\.value \+= 1/)
  assert.match(webview, /h\(ChatTranscriptSection, \{\s*\.\.\.transcriptView\.value,/)
  assert.doesNotMatch(webview, /messages:\s*messages\.value/)
  assert.match(webview, /onLoadEarlier: loadEarlierMessages/)
  assert.match(
    webview,
    /function loadEarlierMessages\(\)[\s\S]*scroll\.beforePrepend\(\)[\s\S]*messageChanges\.afterMessagesChange\(scrollIdentity\.value, messageSequence\.value\)/,
  )
  assert.match(webview, /onLoadEarlier: loadEarlierMessages,\s*\.\.\.transcriptCallbacks\.value,/)
  assert.doesNotMatch(webview, /onViewportReady:\s*\(\(/)
  assert.doesNotMatch(webview, /onScroll:\s*\(\(/)
})

test('ready failure clears stale runtime UI ownership', () => {
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))

  assert.match(
    webview,
    /async applyFailure\(cause, isCurrent\) \{[\s\S]*available\.value = false[\s\S]*chat\.unbindFromInstance\(\)[\s\S]*currentSession\.value = null[\s\S]*sessions\.value = \[\][\s\S]*error\.value = cause\.message/,
  )
})

test('shared conversation CSS is the only message visual source used by both hosts', () => {
  const sharedCss = path.join(frontend, 'components', 'chat', 'shared', 'conversation-message.css')
  const dashboard = read(path.join(frontend, 'components', 'chat', 'ChatMessage.vue'))
  const webview = read(path.join(root, 'src', 'webview', 'index.js'))
  const extensionCss = read(path.join(root, 'src', 'webview', 'style.css'))

  assert.equal(fs.existsSync(sharedCss), true)
  const component = read(path.join(frontend, 'components', 'chat', 'shared', 'ConversationMessage.js'))
  assert.match(component, /import ["']\.\/conversation-message\.css["']/)
  assert.doesNotMatch(dashboard, /conversation-message\.css/)
  assert.doesNotMatch(webview, /conversation-message\.css/)
  assert.doesNotMatch(extensionCss, /\.message\s*\{/)
  assert.doesNotMatch(extensionCss, /\.tool\s*\{/)
  assert.match(extensionCss, /--kt-conversation-accent:/)
})
