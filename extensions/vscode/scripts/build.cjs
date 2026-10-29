const fs = require('node:fs')
const path = require('node:path')
const esbuild = require('esbuild')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'dist')

async function main() {
  fs.rmSync(dist, { recursive: true, force: true })
  fs.mkdirSync(dist, { recursive: true })

  await esbuild.build({
    entryPoints: [path.join(root, 'src', 'extension.cjs')],
    outfile: path.join(dist, 'extension.cjs'),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    target: 'node20',
    external: ['vscode'],
    sourcemap: true,
  })

  const { build } = await import('vite')
  await build({ configFile: path.join(root, 'vite.config.mjs') })

  // Mermaid's own browser build, loaded on the first diagram (src/webview/shims/mermaid.mjs).
  fs.copyFileSync(require.resolve('mermaid/dist/mermaid.min.js', { paths: [root] }), path.join(dist, 'mermaid.js'))
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
