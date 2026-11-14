import { copyFile, mkdir, readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assetFiles, checkedSource, privateDestination } from "./prepare.mjs";

import { WORK_MOTIONS } from "../ui/profiles.js";

export { pngSize, scaledSize } from "./textures.mjs";
import { prepareTextures } from "./textures.mjs";

export async function prepareDeepSeek(source, destination) {
  const manifest = JSON.parse(
    await readFile(await checkedSource(source, "大肥鱼.model3.json"), "utf8"),
  );
  if (manifest.Version !== 3 || !manifest.FileReferences?.Textures?.length)
    throw new Error("Expected a Cubism model3 manifest");
  // Work transitions are loaded separately from the looping idle animation.
  manifest.FileReferences.Motions = {
    Idle: [{ File: "Animation/IDLE.motion3.json" }],
  };
  manifest.FileReferences.Expressions = (await readdir(resolve(source, "Exp3")))
    .filter((name) => name.endsWith(".exp3.json"))
    .sort()
    .map((file) => ({ Name: file.slice(0, -10), File: `Exp3/${file}` }));
  const files = await Promise.all(
    [...new Set([...assetFiles(manifest), ...Object.values(WORK_MOTIONS)])].map(
      async (name) => [name, await checkedSource(source, name)],
    ),
  );
  const dest = privateDestination(destination);
  for (const [name, path] of files) {
    if (manifest.FileReferences.Textures.includes(name)) continue;
    await mkdir(dirname(resolve(dest, name)), { recursive: true });
    await copyFile(path, resolve(dest, name));
  }
  await prepareTextures(
    manifest,
    await Promise.all(
      manifest.FileReferences.Textures.map((name) =>
        checkedSource(source, name),
      ),
    ),
    dest,
    "deepseek",
    2,
  );
  console.info(
    "Originals unchanged. ZIP the destination and import it in Live2D settings.",
  );
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  if (process.argv.length !== 4)
    throw new Error(
      "Usage: node prepare-deepseek.mjs MODEL_FOLDER OUTPUT_FOLDER",
    );
  await prepareDeepSeek(resolve(process.argv[2]), resolve(process.argv[3]));
}
