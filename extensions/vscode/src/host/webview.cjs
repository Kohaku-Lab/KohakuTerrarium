function renderWebviewHtml({ cspSource, scriptUri, styleUri, brandUri = '', nonce }) {
  const policy = [
    "default-src 'none'",
    `script-src 'nonce-${nonce}'`,
    `style-src-elem ${cspSource}`,
    "style-src-attr 'unsafe-inline'",
    `font-src ${cspSource} data:`,
    `img-src ${cspSource} data:`,
    "connect-src 'none'",
  ].join('; ')

  return `<!doctype html>
<html lang="en">
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
