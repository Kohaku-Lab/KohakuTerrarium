import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  assetFiles,
  checkedSource,
  demoManifest,
  localPath,
  privateDestination,
} from "./prepare.mjs";

test("adds existing motions and expressions without changing the original manifest", () => {
  const original = {
    Version: 3,
    FileReferences: {
      Moc: "kohaku.moc3",
      Textures: ["kohaku.4096/texture_00.png"],
    },
  };
  const copy = structuredClone(original);
  const manifest = demoManifest(original);
  assert.deepEqual(original, copy);
  assert.equal(
    manifest.FileReferences.Motions.Sleep[0].File,
    "sleep2.motion3.json",
  );
  assert(assetFiles(manifest).includes("开扇.exp3.json"));
  assert.throws(() => demoManifest({ Version: 3 }), /manifest/);
});

test("rejects absolute, remote and escaping asset references", () => {
  for (const path of [
    "../private.png",
    "/private.png",
    "https://example.com/a.png",
    "a\\b",
    "a/../b",
    "a?token=1",
    "",
  ]) {
    assert.throws(() => localPath(path), /Invalid/);
  }
  assert.equal(
    localPath("kohaku.4096/texture_00.png"),
    "kohaku.4096/texture_00.png",
  );
});

test("refuses symlinks escaping the model folder and missing assets", async () => {
  const temp = await mkdtemp(join(tmpdir(), "kt-live2d-test-"));
  try {
    const folder = join(temp, "model");
    await mkdir(folder);
    await writeFile(join(temp, "outside.png"), "private");
    await symlink(join(temp, "outside.png"), join(folder, "texture.png"));
    await assert.rejects(checkedSource(folder, "texture.png"), /escapes/);
    await assert.rejects(checkedSource(folder, "missing.png"), /ENOENT/);
    await writeFile(join(folder, "inside.png"), "asset");
    assert.equal(
      await checkedSource(folder, "inside.png"),
      await realpath(join(folder, "inside.png")),
    );
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});
test("model preparation refuses package-local destinations", () => {
  assert.throws(
    () => privateDestination(new URL("../ui/model", import.meta.url).pathname),
    /outside/,
  );
  assert.equal(privateDestination(tmpdir()), tmpdir());
});
