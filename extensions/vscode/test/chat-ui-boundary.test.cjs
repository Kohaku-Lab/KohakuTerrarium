const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontendRoot = path.resolve(root, '..', '..', 'src', 'kohakuterrarium-frontend')
const frontendSource = path.join(frontendRoot, 'src')
const publicEntry = path.join(frontendSource, 'public', 'chat', 'index.js')
// Hosts consume the shared chat package through the one public boundary.
const consumers = [
  path.join(frontendSource, 'components', 'chat', 'ChatMessage.vue'),
  path.join(frontendSource, 'components', 'chat', 'ChatPanel.vue'),
  path.join(root, 'src', 'webview', 'index.js'),
  path.join(root, 'src', 'webview', 'transcriptPaging.mjs'),
]
// Production chat leaves are themselves re-exported by the public entry, so they
// must keep reaching their siblings RELATIVELY. Importing the package they are a
// part of would be an import cycle through the very entry that re-exports them.
const leafProducers = [path.join(frontendSource, 'components', 'chat', 'ToolCallBatch.vue')]
const read = (file) => fs.readFileSync(file, 'utf8')

test('Extension independently owns shared Markdown SFC build dependencies', async () => {
  const packageJson = JSON.parse(read(path.join(root, 'package.json')))
  const required = ['@vitejs/plugin-vue', '@vscode/markdown-it-katex', 'highlight.js', 'katex', 'markdown-it']
  const extensionRequire = createRequire(path.join(root, 'package.json'))
  for (const dependency of required) {
    assert.equal(typeof packageJson.devDependencies?.[dependency], 'string', `${dependency} must be direct`)
    const resolved = extensionRequire.resolve(`${dependency}/package.json`)
    assert.ok(resolved.startsWith(path.join(root, 'node_modules') + path.sep), `${dependency}: ${resolved}`)
  }

  const config = (await import(`${pathToFileURL(path.join(root, 'vite.config.mjs')).href}?ownership`)).default
  assert.ok(
    config.plugins.some((plugin) => plugin?.name === 'vite:vue'),
    'Vue SFC plugin must be registered',
  )
  assert.deepEqual(config.resolve.dedupe, ['vue', 'pinia'])
  for (const dependency of [...required, 'vue', 'pinia']) {
    const resolved = extensionRequire.resolve(`${dependency}/package.json`)
    assert.doesNotMatch(resolved, /kohakuterrarium-frontend[\\/]node_modules/, `${dependency}: ${resolved}`)
  }

  const rendererImports = ['markdown-it', '@vscode/markdown-it-katex', 'highlight.js', 'katex/dist/katex.min.css']
  for (const specifier of rendererImports) {
    const alias = config.resolve.alias.find(({ find }) => find instanceof RegExp && find.test(specifier))
    assert.ok(alias, `${specifier} must have an exact Extension-owned alias`)
    assert.equal(alias.find.test(`${specifier}/internal`), false, `${specifier} alias must be exact`)
    assert.equal(fs.existsSync(alias.replacement), true, alias.replacement)
    assert.ok(alias.replacement.startsWith(path.join(root, 'node_modules') + path.sep), alias.replacement)
  }
})

test('Chat UI production consumers use only the public package-style boundary', () => {
  for (const file of consumers) {
    const source = read(file)
    assert.match(source, /from ['"]@kohakuterrarium\/chat-ui['"]/, file)
    assert.doesNotMatch(
      source,
      /components\/chat\/shared|components\/chat\/CommandResultMessage\.vue|utils\/chatToolGrouping|shared\/.+\.css/,
      file,
    )
  }
})

test('Chat UI production leaves import their siblings relatively instead of the package', () => {
  for (const file of leafProducers) {
    const source = read(file)
    // A leaf the entry re-exports must never import that entry: doing so closes
    // an import cycle through the package root.
    assert.doesNotMatch(
      source,
      /from ['"]@kohakuterrarium\/chat-ui['"]/,
      `${file} must not import the entry that re-exports it (import cycle)`,
    )
    // The private ban still applies: a production leaf reaches its siblings by
    // relative path, never through a private shared/internal module.
    assert.doesNotMatch(source, /components\/chat\/shared|utils\/chatToolGrouping|shared\/.+\.css/, file)
    assert.match(source, /from ['"]\.\.?\//, `${file} must import its siblings relatively`)
  }
})

test('public entry owns the required API and component CSS', () => {
  assert.equal(fs.existsSync(publicEntry), true)
  const entry = read(publicEntry)
  for (const symbol of [
    'CommandResultMessage',
    'ConversationMessage',
    'ChatTranscriptSection',
    'MarkdownRenderer',
    'DEFAULT_TOOL_BATCH_THRESHOLD',
    'computeRenderGroups',
    'summarizeBatch',
  ])
    assert.match(entry, new RegExp(`\\b${symbol}\\b`))

  // Identity: the entry re-exports the one production command-result leaf, so
  // the Dashboard and the VS Code webview render the same component instead of
  // forking a second copy behind a private path.
  assert.match(entry, /export \{ default as CommandResultMessage \} from "\.\.\/\.\.\/components\/chat\/CommandResultMessage\.vue"/)

  // The shared tool leaves and the nested sub-agent surface are the production
  // components both hosts render; each is re-exported by the one public entry so
  // neither host forks a second copy behind a private path.
  for (const leaf of ['UIEventBlock', 'ToolCallBlock', 'ToolCallBatch', 'SubagentConversationPanel', 'VideoFilePreview']) {
    assert.match(entry, new RegExp(`export \\{ default as ${leaf} \\}`), leaf)
  }

  const message = read(path.join(frontendSource, 'components/chat/shared/ConversationMessage.js'))
  const transcript = read(path.join(frontendSource, 'components/chat/shared/ChatTranscriptSection.js'))
  assert.match(message, /import ['"]\.\/conversation-message\.css['"]/)
  assert.match(transcript, /import ['"]\.\/chat-transcript-section\.css['"]/)
})

test('public Markdown graph is recursively host-neutral', () => {
  const publicRoot = path.join(frontendSource, 'public', 'chat')
  const pending = [path.join(publicRoot, 'MarkdownRenderer.vue')]
  const visited = new Set()
  const forbidden = /(?:^@\/|element-plus|vue-router|\/stores\/|modelInventory|components\/(?!public\/chat)|\b(?:window|document)\b)/

  while (pending.length) {
    const file = pending.pop()
    if (visited.has(file)) continue
    visited.add(file)
    const source = read(file)
    const imports = [...source.matchAll(/(?:from\s*|import\s*)['"]([^'"]+)['"]/g)].map((match) => match[1])
    for (const specifier of imports) {
      assert.doesNotMatch(specifier, forbidden, `${file}: ${specifier}`)
      if (!specifier.startsWith('.')) continue
      const resolved = path.resolve(path.dirname(file), specifier)
      assert.ok(resolved.startsWith(publicRoot + path.sep), `${file}: ${specifier}`)
      pending.push(resolved)
    }
    if (!file.endsWith('MarkdownRenderer.vue')) {
      const executable = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
      assert.doesNotMatch(executable, /\b(?:window|document)\b/, file)
    }
  }
})

test('public aliases match only the package root and precede compatibility @', async () => {
  const configFiles = [
    path.join(root, 'vite.config.mjs'),
    path.join(frontendRoot, 'vite.config.js'),
    path.join(frontendRoot, 'vitest.config.js'),
  ]
  for (const file of configFiles) {
    const config = (await import(pathToFileURL(file).href)).default
    const aliases = config.resolve.alias
    const publicIndex = aliases.findIndex(({ find }) => find instanceof RegExp && find.test('@kohakuterrarium/chat-ui'))
    const genericIndex = aliases.findIndex(({ find }) => find === '@')
    assert.ok(publicIndex >= 0 && genericIndex > publicIndex, file)
    const publicFind = aliases[publicIndex].find
    assert.equal(publicFind.test('@kohakuterrarium/chat-ui'), true, file)
    assert.equal(publicFind.test('@kohakuterrarium/chat-ui/private'), false, file)
  }

  const webviewFiles = fs.readdirSync(path.join(root, 'src', 'webview'), { recursive: true }).filter((name) => /\.(?:js|vue)$/.test(name))
  for (const name of webviewFiles) {
    const source = read(path.join(root, 'src', 'webview', name))
    const privateImports = [...source.matchAll(/from ['"](@\/[^'"]+)['"]/g)].map((match) => match[1])
    assert.deepEqual(
      privateImports.filter((specifier) => specifier !== '@/stores/chat'),
      [],
      name,
    )
  }
})
