function hostLanguage() {
  // The host language is the locale seam every webview inherits. Read it here
  // so the renderer defaults to ``vscode.env.language`` without the caller
  // threading it through; tests without the ``vscode`` module fall back to English.
  try {
    const vscode = require('vscode')
    return vscode?.env?.language || 'en'
  } catch {
    return 'en'
  }
}

function renderWebviewHtml({ cspSource, scriptUri, styleUri, brandUri = '', locale = hostLanguage(), nonce, mediaSrc = '' }) {
  const policy = [
    "default-src 'none'",
    `script-src 'nonce-${nonce}'`,
    `style-src-elem ${cspSource}`,
    "style-src-attr 'unsafe-inline'",
    `font-src ${cspSource} data:`,
    `img-src ${cspSource} data:`,
    // The media surface is only the Host-spooled file exposed through
    // asWebviewUri, so it is granted only when the Host wires a media source and
    // never a remote URL.
    ...(mediaSrc ? [`media-src ${mediaSrc}`] : []),
    "connect-src 'none'",
  ].join('; ')

  // Keep ``lang`` to a language-tag shape so it can never break out of the attribute.
  const lang = String(locale || 'en').replace(/[^A-Za-z0-9_-]/g, '') || 'en'

  return `<!doctype html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${policy}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="${styleUri}">
  <title>KohakuTerrarium</title>
</head>
<body>
  <main id="app" aria-live="polite" data-brand-uri="${brandUri}"></main>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`
}

module.exports = { renderWebviewHtml }
