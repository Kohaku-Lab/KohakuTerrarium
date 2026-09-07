const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const { execFileSync } = require('node:child_process')
const yauzl = require('yauzl')

const ROOT = path.resolve(__dirname, '..')

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), 'utf8')
}

function readZipEntry(archive, entryName) {
  return new Promise((resolve, reject) => {
    yauzl.open(archive, { lazyEntries: true }, (openError, zip) => {
      if (openError) return reject(openError)
      let settled = false
      const finish = (error, value) => {
        if (settled) return
        settled = true
        zip.close()
        if (error) reject(error)
        else resolve(value)
      }
      zip.readEntry()
      zip.on('entry', (entry) => {
        if (entry.fileName !== entryName) return zip.readEntry()
        zip.openReadStream(entry, (streamError, stream) => {
          if (streamError) return finish(streamError)
          const chunks = []
          stream.on('data', (chunk) => chunks.push(chunk))
          stream.on('end', () => finish(null, Buffer.concat(chunks).toString('utf8')))
          stream.on('error', finish)
        })
      })
      zip.on('end', () => finish(Error(`${entryName} not found in ${archive}`)))
      zip.on('error', finish)
    })
  })
}

test('CI validates Extension tests, build, package, and public chat boundary', () => {
  const workflow = fs.readFileSync(path.resolve(ROOT, '..', '..', '.github', 'workflows', 'ci.yml'), 'utf8').replaceAll('\r\n', '\n')
  const start = workflow.indexOf('  vscode-extension-check:\n')
  assert.notEqual(start, -1)
  const jobTail = workflow.slice(start + 2)
  const nextJob = jobTail.search(/\n  [a-z][a-z0-9-]*:\n/)
  const job = nextJob === -1 ? jobTail : jobTail.slice(0, nextJob)

  assert.match(job, /\n    runs-on: ubuntu-latest\n/)
  assert.match(job, /\n    defaults:\n      run:\n        working-directory: extensions\/vscode\n/)
  assert.match(job, /\n          node-version: "24"\n/)
  assert.match(job, /\n          cache: npm\n/)
  assert.match(job, /\n          cache-dependency-path: extensions\/vscode\/package-lock\.json\n/)
  const commands = [...job.matchAll(/^      - run: (.+)$/gm)].map((match) => match[1])
  assert.deepEqual(commands, ['npm ci', 'npm run format:check', 'npm test', 'npm run build', 'npm run package'])
  assert.match(
    job,
    /- name: Install frontend config dependencies\n        working-directory: src\/kohakuterrarium-frontend\n        run: npm ci\n/,
  )
  assert.match(job, /- name: Verify public chat import boundary\n        run: node --test test\/chat-ui-boundary\.test\.cjs\n/)
  assert.match(job, /- name: Verify packaged extension\n        shell: bash\n        run: \|\n/)
  for (const contract of [
    'test "${#packages[@]}" -eq 1',
    'test -s "${packages[0]}"',
    'node node_modules/@vscode/vsce/vsce ls',
    "grep -Fx 'LICENSE'",
    "grep -Fx 'dist/extension.cjs'",
    "grep -Fx 'dist/webview.js'",
    "grep -Fx 'dist/webview.css'",
    "! grep -E '\\.(map|test\\.cjs)$|^node_modules/'",
  ]) {
    assert.ok(job.includes(contract), contract)
  }
})

test('manifest defines a workspace sidebar extension and deterministic package scripts', () => {
  const manifest = JSON.parse(read('package.json'))

  assert.equal(manifest.name, 'kohakuterrarium-vscode')
  assert.equal(manifest.private, true)
  assert.equal(manifest.main, './dist/extension.cjs')
  assert.deepEqual(manifest.extensionKind, ['workspace'])
  assert.ok(manifest.activationEvents.includes('onView:kohakuterrarium.chat'))
  assert.deepEqual(manifest.contributes.views.kohakuterrarium, [{ type: 'webview', id: 'kohakuterrarium.chat', name: 'KohakuTerrarium' }])
  assert.deepEqual(manifest.contributes.commands, [
    {
      command: 'kohakuterrarium.configure',
      title: 'KohakuTerrarium: Configure Local Connection Override',
    },
    {
      command: 'kohakuterrarium.useAutomaticDiscovery',
      title: 'KohakuTerrarium: Use Automatic Local Discovery',
    },
  ])
  assert.equal(manifest.contributes.configuration.properties['kohakuterrarium.defaultCreature'].scope, 'machine')
  assert.equal(manifest.scripts.format, 'prettier --write .prettierrc.json scripts/ src/ test/ package.json vite.config.mjs')
  assert.equal(manifest.scripts['format:check'], 'prettier --check .prettierrc.json scripts/ src/ test/ package.json vite.config.mjs')
  assert.equal(manifest.scripts.test, 'node --test test/*.test.cjs')
  assert.equal(manifest.scripts.build, 'node scripts/build.cjs')
  assert.equal(manifest.scripts.package, 'npm run build && vsce package --no-dependencies')
  assert.equal(manifest.repository.url, 'https://github.com/Kohaku-Lab/KohakuTerrarium')
  assert.equal(manifest.license, 'LicenseRef-KohakuTerrarium-1.0')
  const lockManifest = JSON.parse(read('package-lock.json')).packages['']
  assert.equal(lockManifest.license, manifest.license)
  assert.equal(manifest.devDependencies.prettier, '^3.8.2')
  assert.equal(lockManifest.devDependencies.prettier, manifest.devDependencies.prettier)
  const rootLicense = fs.readFileSync(path.resolve(ROOT, '..', '..', 'LICENSE'), 'utf8')
  assert.equal(read('LICENSE'), rootLicense)
  assert.match(rootLicense, /KohakuTerrarium License/)
  assert.match(rootLicense, /Naming Requirement/)
  assert.equal(manifest.files, undefined)
  const ignored = read('.vscodeignore')
  for (const entry of ['src/**', 'test/**', 'scripts/**', 'node_modules/**', 'package-lock.json']) {
    assert.match(ignored, new RegExp(entry.replaceAll('*', '\\*')))
  }
})

test('packaged VSIX carries the repository license', async () => {
  const archive = path.join(ROOT, 'kohakuterrarium-vscode-license-test.vsix')
  const dist = path.join(ROOT, 'dist')
  const distBackup = path.join(ROOT, `dist.license-test-${process.pid}`)
  const hadDist = fs.existsSync(dist)
  fs.rmSync(distBackup, { recursive: true, force: true })
  if (hadDist) fs.renameSync(dist, distBackup)
  try {
    execFileSync(process.execPath, [path.join(ROOT, 'scripts', 'build.cjs')], {
      cwd: ROOT,
      stdio: 'pipe',
    })
    execFileSync(
      process.execPath,
      [path.join(ROOT, 'node_modules', '@vscode', 'vsce', 'vsce'), 'package', '--no-dependencies', '--out', archive],
      { cwd: ROOT, stdio: 'pipe' },
    )
    assert.equal(await readZipEntry(archive, 'extension/LICENSE.txt'), read('LICENSE'))
  } finally {
    fs.rmSync(archive, { force: true })
    fs.rmSync(dist, { recursive: true, force: true })
    if (hadDist) fs.renameSync(distBackup, dist)
  }
})

test('fixed protocol accepts only shell messages and rejects host capabilities from the webview', () => {
  const { allowedMessage } = require('../src/host/protocol.cjs')

  for (const message of [
    { type: 'ready', requestId: 1 },
    { type: 'session.list', requestId: 2 },
    { type: 'session.create', requestId: 3 },
  ]) {
    assert.equal(allowedMessage(message), true)
  }

  for (const message of [
    { type: 'session.create', requestId: 4, token: 'secret' },
    { type: 'session.create', requestId: 5, configPath: 'C:/secret/config' },
    { type: 'session.create', requestId: 6, pwd: 'C:/workspace' },
    { type: 'unknown', id: 7 },
    { type: 'ready' },
  ]) {
    assert.equal(allowedMessage(message), false)
  }
})

test('webview shell uses a strict CSP and only packaged resources', () => {
  const { renderWebviewHtml } = require('../src/host/webview.cjs')
  const html = renderWebviewHtml({
    cspSource: 'vscode-webview://origin',
    scriptUri: 'vscode-webview://origin/dist/webview.js',
    styleUri: 'vscode-webview://origin/dist/webview.css',
    nonce: 'fixed-test-nonce',
  })

  assert.match(html, /default-src 'none'/)
  const policy = html.match(/Content-Security-Policy" content="([^"]+)"/)?.[1]
  assert.deepEqual(
    new Set(policy?.split('; ').map((directive) => directive.trim())),
    new Set([
      "default-src 'none'",
      "script-src 'nonce-fixed-test-nonce'",
      'style-src-elem vscode-webview://origin',
      "style-src-attr 'unsafe-inline'",
      'font-src vscode-webview://origin data:',
      'img-src vscode-webview://origin data:',
      "connect-src 'none'",
    ]),
  )
  assert.match(html, /src="vscode-webview:\/\/origin\/dist\/webview\.js"/)
  assert.match(html, /href="vscode-webview:\/\/origin\/dist\/webview\.css"/)
  assert.doesNotMatch(policy, /script-src[^;]*(?:unsafe-inline|unsafe-eval|https?:)/)
  assert.doesNotMatch(policy, /style-src-elem[^;]*https?:/)
  assert.doesNotMatch(html, /https?:\/\//)
})

test('build script is self-contained inside the formal extension package', () => {
  const build = read('scripts/build.cjs')

  assert.match(build, /require\(['"]esbuild['"]\)/)
  assert.match(build, /path\.join\(root, 'src', 'extension\.cjs'\)/)
  assert.match(build, /path\.join\(root, 'vite\.config\.mjs'\)/)
  assert.doesNotMatch(build, /vscode-chat-sidebar-tracer/)
  assert.doesNotMatch(build, /kohakuterrarium-frontend[\\/]node_modules/)
})
