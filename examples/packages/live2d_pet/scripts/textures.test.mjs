import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";
import { deflateSync } from "node:zlib";
import { prepareTextures, webpSize } from "./textures.mjs";

const run = promisify(execFile);
const encoder = process.env.CWEBP || "cwebp";
const decoder = process.env.DWEBP || "dwebp";
let canEncode = process.platform === "darwin";
try {
  await run(encoder, ["-version"]);
  await run(decoder, ["-version"]);
} catch {
  canEncode = false;
}

function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type), data]);
  let crc = 0xffffffff;
  for (const byte of body) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length);
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([head, body, tail]);
}

function png(pixels, metadata = []) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(4, 0);
  header.writeUInt32BE(4, 4);
  header[8] = 8;
  header[9] = 6;
  const scanlines = [];
  for (let row = 0; row < 4; row++)
    scanlines.push(Buffer.from([0]), pixels.subarray(row * 16, (row + 1) * 16));
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    chunk("IHDR", header),
    ...metadata,
    chunk("IDAT", deflateSync(Buffer.concat(scanlines))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function webp(type, data) {
  const header = Buffer.alloc(20);
  header.write("RIFF");
  header.writeUInt32LE(12 + data.length + (data.length % 2), 4);
  header.write("WEBP", 8);
  header.write(type, 12);
  header.writeUInt32LE(data.length, 16);
  return Buffer.concat([header, data, Buffer.alloc(data.length % 2)]);
}

const pixels = Buffer.from(
  Array.from({ length: 16 }, (_, i) => [
    i * 13,
    255 - i * 11,
    i * 7,
    i % 3 ? 191 : 0,
  ]).flat(),
);
const manifest = {
  Version: 3,
  FileReferences: { Moc: "model.moc3", Textures: ["source.png"] },
};

test(
  "encodes exact lossless WebP offline and removes only superseded generated PNGs",
  { skip: !canEncode },
  async () => {
    const temp = await mkdtemp(join(tmpdir(), "kt-live2d-webp-"));
    try {
      const source = join(temp, "source.png");
      const destination = join(temp, "model");
      const icc = await readFile(
        "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
      );
      const original = png(pixels, [
        chunk(
          "iCCP",
          Buffer.concat([Buffer.from("sRGB\0\0"), deflateSync(icc)]),
        ),
      ]);
      const initialManifest = structuredClone(manifest);
      await writeFile(source, original);
      for (const quality of ["high", "standard"]) {
        await mkdir(join(destination, quality), { recursive: true });
        await writeFile(
          join(destination, quality, "texture_0.png"),
          "old atlas",
        );
        await writeFile(
          join(destination, quality, "texture_8.png"),
          "unowned atlas",
        );
        await writeFile(join(destination, quality, "custom.png"), "keep me");
      }
      await prepareTextures(manifest, [source], destination, "fixture", 1, {
        encoder,
      });
      assert.deepEqual(manifest, initialManifest);
      assert.deepEqual(await readFile(source), original);
      for (const [quality, edge] of [
        ["high", 4],
        ["standard", 2],
      ]) {
        const prepared = JSON.parse(
          await readFile(join(destination, `${quality}.model3.json`)),
        );
        assert.deepEqual(prepared.FileReferences.Textures, [
          `${quality}/texture_0.webp`,
        ]);
        assert.equal(prepared.Demo.textureBytes, edge * edge * 4);
        assert.deepEqual(prepared.KohakuTerrarium, { adapter: "fixture" });
        assert.equal(
          prepared.Demo.textures[0].file,
          `${quality}/texture_0.webp`,
        );
        const output = await readFile(
          join(destination, prepared.FileReferences.Textures[0]),
        );
        assert.deepEqual(webpSize(output), { width: edge, height: edge });
        if (quality === "high")
          assert(
            output.includes(icc),
            "embedded ICC profile bytes survive encoding",
          );
        assert.deepEqual((await readdir(join(destination, quality))).sort(), [
          "custom.png",
          "texture_0.webp",
          "texture_8.png",
        ]);
      }
      const decoded = join(temp, "decoded.pam");
      await run(decoder, [
        join(destination, "high/texture_0.webp"),
        "-pam",
        "-o",
        decoded,
      ]);
      const pam = await readFile(decoded);
      const end = pam.indexOf(Buffer.from("ENDHDR\n")) + 7;
      assert.deepEqual(
        pam.subarray(end),
        pixels,
        "fully transparent RGB and every RGBA byte survive",
      );
      await prepareTextures(manifest, [source], destination, "fixture", 1, {
        encoder,
      });
      assert.equal(
        (await readdir(destination)).some((name) =>
          name.startsWith(".textures-"),
        ),
        false,
      );
    } finally {
      await rm(temp, { recursive: true, force: true });
    }
  },
);

test("rejects corrupt, lossy, animated and inconsistent WebP outputs", () => {
  const validHeader = Buffer.from([0x2f, 3, 0xc0, 0, 0, 0]);
  const valid = webp("VP8L", validHeader);
  const inconsistent = Buffer.concat([
    webp("VP8X", Buffer.alloc(10)),
    valid.subarray(12),
  ]);
  inconsistent.writeUInt32LE(inconsistent.length - 8, 4);
  assert.deepEqual(webpSize(valid), { width: 4, height: 4 });
  for (const bytes of [
    Buffer.from("broken"),
    valid.subarray(0, -1),
    inconsistent,
    webp("VP8 ", Buffer.alloc(12)),
    webp("ANIM", Buffer.alloc(6)),
    webp("VP8L", Buffer.from([0x2f, 0, 0])),
    webp("VP8L", Buffer.from([0x2f, 0, 0, 0, 0xe0, 0])),
  ])
    assert.throws(() => webpSize(bytes), /WebP/);
});

test("encoder failures preserve the old manifests and textures", async () => {
  const temp = await mkdtemp(join(tmpdir(), "kt-live2d-webp-failure-"));
  try {
    const source = join(temp, "source.png");
    const destination = join(temp, "model");
    await writeFile(source, png(pixels));
    await mkdir(join(destination, "high"), { recursive: true });
    await writeFile(join(destination, "high.model3.json"), "old manifest");
    await writeFile(join(destination, "high/texture_0.png"), "old PNG");
    await assert.rejects(
      prepareTextures(manifest, [source], destination, "fixture", 1, {
        encoder: join(temp, "missing-cwebp"),
      }),
      /cwebp is required/,
    );
    const brokenEncoder = join(temp, "broken-cwebp");
    await writeFile(
      brokenEncoder,
      `#!${process.execPath}\nimport { writeFileSync } from "node:fs";\nif (!process.argv.includes("-version")) writeFileSync(process.argv[process.argv.indexOf("-o") + 1], "invalid WebP");\n`,
    );
    await chmod(brokenEncoder, 0o755);
    await assert.rejects(
      prepareTextures(manifest, [source], destination, "fixture", 1, {
        encoder: brokenEncoder,
      }),
      /WebP/,
    );
    assert.equal(
      await readFile(join(destination, "high.model3.json"), "utf8"),
      "old manifest",
    );
    assert.equal(
      await readFile(join(destination, "high/texture_0.png"), "utf8"),
      "old PNG",
    );
    assert.deepEqual((await readdir(destination)).sort(), [
      "high",
      "high.model3.json",
    ]);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test(
  "refuses unsupported PNG precision, animation and color information",
  { skip: !canEncode },
  async () => {
    const temp = await mkdtemp(join(tmpdir(), "kt-live2d-color-"));
    try {
      const gamma = Buffer.alloc(4);
      gamma.writeUInt32BE(100000);
      const source = join(temp, "source.png");
      const highPrecision = png(pixels);
      highPrecision[24] = 16;
      for (const [bytes, error] of [
        [png(pixels, [chunk("gAMA", gamma)]), /Non-sRGB.*ICC/],
        [highPrecision, /above 8 bits/],
        [png(pixels, [chunk("acTL", Buffer.alloc(8))]), /Animated PNG/],
      ]) {
        await writeFile(source, bytes);
        await assert.rejects(
          prepareTextures(
            manifest,
            [source],
            join(temp, "model"),
            "fixture",
            1,
            { encoder },
          ),
          error,
        );
      }
    } finally {
      await rm(temp, { recursive: true, force: true });
    }
  },
);
