const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const test = require('node:test')
const frontendRequire = createRequire(path.resolve(__dirname, '../../../src/kohakuterrarium-frontend/package.json'))
const { JSDOM } = frontendRequire('jsdom')
const image = 'data:image/png;base64,iVBORw0KGgo='
const url = '/api/sessions/saved/artifacts/generated_images/pic.png'
const tick = () => new Promise((resolve) => setImmediate(resolve))

test('artifact loader deduplicates bounded reads, fences old responses and drops cache on owner change', async () => {
  const { createArtifactLoader } = await import('../src/webview/artifactImages.mjs')
  let fence = { readyId: 1, selectionVersion: 0 }
  const calls = []
  const loader = createArtifactLoader({
    getFence: () => fence,
    request: (...args) => new Promise((resolve) => calls.push({ args, resolve })),
    maxConcurrent: 1,
    maxEntries: 2,
  })
  const a = loader.load(url)
  const b = loader.load(url)
  await tick()
  assert.equal(calls.length, 1)
  calls[0].resolve({ dataUrl: image })
  assert.equal(await a, image)
  assert.equal(await b, image)
  await loader.load(url)
  assert.equal(calls.length, 1)
  const old = loader.load(url.replace('pic', 'old'))
  const rejected = assert.rejects(old, /ownership|disposed/)
  await tick()
  fence = { readyId: 2, selectionVersion: 0 }
  loader.reset()
  calls[1].resolve({ dataUrl: image })
  await rejected
  const fresh = loader.load(url)
  await tick()
  calls[2].resolve({ dataUrl: image })
  assert.equal(await fresh, image)
  await assert.rejects(loader.load('https://other/api/sessions/saved/artifacts/p.png'))
  await assert.rejects(loader.load('/api/sessions/saved/artifacts/%252e%252e/secret'))
  await assert.rejects(loader.load('/api/sessions/saved/artifacts/%25252e%25252e%25252fsecret'))
  loader.dispose()
  await assert.rejects(loader.load(url))
})

test('artifact DOM adapter handles Markdown/images safely and ignores late work after unmount', async () => {
  const { observeArtifactImages } = await import('../src/webview/artifactImages.mjs')
  const dom = new JSDOM(
    `<section><p><img alt="markdown pic" src="${url}"></p><img src="data:image/png;base64,old"><code>![not image](${url})</code></section>`,
  )
  const root = dom.window.document.querySelector('section')
  const calls = []
  const stop = observeArtifactImages(root, { load: (path) => new Promise((resolve, reject) => calls.push({ path, resolve, reject })) })
  await tick()
  assert.equal(calls.length, 1)
  calls[0].resolve(image)
  await tick()
  assert.equal(root.querySelector('p img').getAttribute('src'), image)
  assert.equal(root.querySelectorAll('img')[1].getAttribute('src'), 'data:image/png;base64,old')
  const another = dom.window.document.createElement('img')
  another.src = url.replace('pic', 'failure')
  root.append(another)
  await tick()
  calls[1].reject(Error('token=secret'))
  await tick()
  assert.match(root.textContent, /Image unavailable/)
  assert.doesNotMatch(root.textContent, /secret/)
  root.querySelector('p img').setAttribute('src', url.replace('pic', 'swap-a'))
  await tick()
  root.querySelector('p img').setAttribute('src', url.replace('pic', 'swap-b'))
  await tick()
  calls[2].resolve(image)
  calls[3].resolve(image)
  await tick()
  assert.equal(root.querySelector('p').textContent, '', 'replaced source never retains loading status')
  const retry = root.querySelector('button[aria-label="Retry image"]')
  assert.ok(retry)
  retry.click()
  await tick()
  calls[4].resolve(image)
  await tick()
  assert.equal(another.getAttribute('src'), image)
  const late = dom.window.document.createElement('img')
  late.src = url.replace('pic', 'late')
  root.append(late)
  await tick()
  stop()
  calls[5].resolve(image)
  await tick()
  assert.notEqual(late.getAttribute('src'), image)
  dom.window.close()
})
