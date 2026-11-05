import {
  copyFile,
  cp,
  mkdir,
  readFile,
  realpath,
  stat,
  writeFile,
} from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { prepareTextures } from "./textures.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function localPath(name) {
  if (
    typeof name !== "string" ||
    !name ||
    isAbsolute(name) ||
    name.includes("\\") ||
    name.split("/").some((p) => !p || p === "." || p === "..") ||
    /[:?#]/.test(name)
  ) {
    throw new Error(`Invalid local asset path: ${name}`);
  }
  return name;
}

export function demoManifest(original) {
  if (
    original.Version !== 3 ||
    !original.FileReferences?.Moc ||
    !original.FileReferences?.Textures?.length
  ) {
    throw new Error(
      "A Cubism model3 manifest with Moc and Textures is required.",
    );
  }
  const manifest = structuredClone(original);
  manifest.FileReferences.Motions = {
    Idle: [{ File: "idle.motion3.json", FadeInTime: 0.5, FadeOutTime: 0.5 }],
    Sleep: [{ File: "sleep2.motion3.json", FadeInTime: 0.7, FadeOutTime: 0.5 }],
  };
  manifest.FileReferences.Expressions = [
    "抬手",
    "抬手2",
    "开扇",
    "扇面",
    "变",
  ].map((Name) => ({ Name, File: `${Name}.exp3.json` }));
  return manifest;
}

export function assetFiles(manifest) {
  const refs = manifest.FileReferences;
  return [
    ...new Set(
      [
        refs.Moc,
        ...refs.Textures,
        refs.Physics,
        refs.DisplayInfo,
        ...Object.values(refs.Motions)
          .flat()
          .map((m) => m.File),
        ...refs.Expressions.map((e) => e.File),
      ]
        .filter(Boolean)
        .map(localPath),
    ),
  ];
}

export async function checkedSource(base, name) {
  const source = await realpath(resolve(base, localPath(name)));
  const rel = relative(await realpath(base), source);
  if (
    rel === ".." ||
    rel.startsWith(`..${sep}`) ||
    isAbsolute(rel) ||
    !(await stat(source)).isFile()
  ) {
    throw new Error(`Asset escapes its source folder: ${name}`);
  }
  return source;
}

export function privateDestination(destination) {
  const path = resolve(destination);
  const rel = relative(root, path);
  if (!rel || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel)))
    throw new Error("Model output must be outside the extension package.");
  return path;
}

export async function prepare(modelDir, sdkDir, outputDir) {
  const destinationRoot = privateDestination(outputDir);
  const manifest = demoManifest(
    JSON.parse(await readFile(resolve(modelDir, "kohaku.model3.json"), "utf8")),
  );
  const files = await Promise.all(
    assetFiles(manifest).map(async (name) => [
      name,
      await checkedSource(modelDir, name),
    ]),
  );
  const sdkInfo = await readFile(resolve(sdkDir, "cubism-info.yml"), "utf8");
  if (!/^version: 5-r\.5\s*$/m.test(sdkInfo))
    throw new Error("Use the official Cubism SDK for Web 5-r.5 archive.");
  await mkdir(destinationRoot, { recursive: true });
  for (const [name, source] of files) {
    const destination = resolve(destinationRoot, name);
    await mkdir(dirname(destination), { recursive: true });
    await copyFile(source, destination);
  }
  await writeFile(
    resolve(destinationRoot, "kohaku.model3.json"),
    JSON.stringify(manifest, null, 2),
  );
  await prepareTextures(
    manifest,
    await Promise.all(
      manifest.FileReferences.Textures.map((name) =>
        checkedSource(modelDir, name),
      ),
    ),
    destinationRoot,
    "dragon",
    1,
  );
  await cp(resolve(sdkDir, "Framework"), resolve(root, "vendor/Framework"), {
    recursive: true,
  });
  await mkdir(resolve(root, "ui/runtime"), { recursive: true });
  await copyFile(
    resolve(sdkDir, "Core/live2dcubismcore.min.js"),
    resolve(root, "ui/runtime/live2dcubismcore.min.js"),
  );
  await cp(
    resolve(sdkDir, "Framework/Shaders/WebGL"),
    resolve(root, "ui/runtime/shaders"),
    { recursive: true },
  );
  await copyFile(
    resolve(sdkDir, "LICENSE.md"),
    resolve(root, "ui/runtime/SDK-LICENSE.md"),
  );
  await copyFile(
    resolve(sdkDir, "Framework/LICENSE.md"),
    resolve(root, "ui/runtime/Framework-LICENSE.md"),
  );
  await copyFile(
    resolve(sdkDir, "cubism-info.yml"),
    resolve(root, "ui/runtime/cubism-info.yml"),
  );
  console.info(
    `Prepared ${files.length} original model assets. Originals were not changed.`,
  );
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  if (process.argv.length !== 5)
    throw new Error("Usage: node scripts/prepare.mjs MODEL_FOLDER SDK_FOLDER PRIVATE_OUTPUT_FOLDER");
  await prepare(resolve(process.argv[2]), resolve(process.argv[3]), resolve(process.argv[4]));
}
