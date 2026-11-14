import { build } from "../../../../src/kohakuterrarium-frontend/node_modules/vite/dist/node/index.js";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
await build({
  configFile: false,
  root,
  build: {
    emptyOutDir: false,
    outDir: resolve(root, "ui/runtime"),
    lib: {
      entry: resolve(root, "src/player.js"),
      formats: ["es"],
      fileName: () => "player.js",
    },
    minify: false,
  },
});
