import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { inflateSync } from "node:zlib";
const run = promisify(execFile);

export function pngSize(bytes) {
  if (
    bytes.length < 24 ||
    bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a"
  )
    throw new Error("Expected a PNG texture");
  const width = bytes.readUInt32BE(16),
    height = bytes.readUInt32BE(20);
  if (!width || !height) throw new Error("Empty texture");
  return { width, height };
}

export function scaledSize(size, divisor) {
  if (
    ![1, 2, 4].includes(divisor) ||
    !Number.isInteger(size.width) ||
    !Number.isInteger(size.height) ||
    size.width <= 0 ||
    size.height <= 0 ||
    size.width % divisor ||
    size.height % divisor
  )
    throw new Error("Texture dimensions must divide exactly by 1, 2 or 4");
  return { width: size.width / divisor, height: size.height / divisor };
}

function pngInfo(bytes) {
  const size = pngSize(bytes);
  if (bytes[24] > 8)
    throw new Error("PNG textures above 8 bits per channel are not supported");
  let offset = 8;
  let icc = null;
  let gamma = null;
  let chromaticities = null;
  let ended = false;
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString("ascii", offset + 4, offset + 8);
    if (offset + length + 12 > bytes.length)
      throw new Error("Truncated PNG texture");
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    if (["acTL", "fcTL", "fdAT"].includes(type))
      throw new Error("Animated PNG textures are not supported");
    if (type === "iCCP") {
      const separator = data.indexOf(0);
      if (separator < 1 || data[separator + 1] !== 0)
        throw new Error("Invalid PNG ICC profile");
      icc = inflateSync(data.subarray(separator + 2), {
        maxOutputLength: 16 * 1024 * 1024,
      });
    } else if (type === "gAMA") {
      if (length !== 4) throw new Error("Invalid PNG gamma");
      gamma = data.readUInt32BE(0);
    } else if (type === "cHRM") {
      if (length !== 32) throw new Error("Invalid PNG chromaticities");
      chromaticities = Array.from({ length: 8 }, (_, i) =>
        data.readUInt32BE(i * 4),
      );
    }
    offset += length + 12;
    if (type === "IEND") {
      ended = length === 0 && offset === bytes.length;
      break;
    }
  }
  if (!ended) throw new Error("Incomplete PNG texture");
  const srgb = [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000];
  if (
    !icc &&
    ((gamma !== null && gamma !== 45455) ||
      (chromaticities && chromaticities.some((value, i) => value !== srgb[i])))
  )
    throw new Error("Non-sRGB PNG color information requires an ICC profile");
  return { ...size, icc };
}

function webpInfo(bytes) {
  if (
    bytes.length < 12 ||
    bytes.toString("ascii", 0, 4) !== "RIFF" ||
    bytes.toString("ascii", 8, 12) !== "WEBP" ||
    bytes.readUInt32LE(4) + 8 !== bytes.length
  )
    throw new Error("Invalid WebP texture container");
  let offset = 12;
  let size = null;
  let canvas = null;
  let icc = null;
  while (offset + 8 <= bytes.length) {
    const type = bytes.toString("ascii", offset, offset + 4);
    const length = bytes.readUInt32LE(offset + 4);
    const end = offset + 8 + length;
    if (end + (length % 2) > bytes.length)
      throw new Error("Truncated WebP texture");
    const data = bytes.subarray(offset + 8, end);
    if (type === "VP8L") {
      if (size || length < 6 || data[0] !== 0x2f)
        throw new Error("Invalid lossless WebP texture");
      const bits = data.readUInt32LE(1);
      if (bits >>> 29) throw new Error("Unsupported lossless WebP version");
      size = {
        width: (bits & 0x3fff) + 1,
        height: ((bits >>> 14) & 0x3fff) + 1,
      };
    } else if (type === "VP8X") {
      if (length !== 10 || canvas || data[0] & 0x02)
        throw new Error("Invalid or animated WebP texture");
      canvas = {
        width: data.readUIntLE(4, 3) + 1,
        height: data.readUIntLE(7, 3) + 1,
      };
    } else if (["VP8 ", "ANIM", "ANMF"].includes(type)) {
      throw new Error("Expected a static lossless WebP texture");
    } else if (type === "ICCP") {
      if (icc) throw new Error("Duplicate WebP ICC profile");
      icc = data;
    }
    offset = end + (length % 2);
  }
  if (
    !size ||
    offset !== bytes.length ||
    (canvas && (canvas.width !== size.width || canvas.height !== size.height))
  )
    throw new Error("Incomplete or inconsistent lossless WebP texture");
  return { ...size, icc };
}

export function webpSize(bytes) {
  const { width, height } = webpInfo(bytes);
  return { width, height };
}

export async function prepareTextures(
  manifest,
  sources,
  destination,
  profile,
  highDivisor,
  { encoder = process.env.CWEBP || "cwebp" } = {},
) {
  try {
    await run(encoder, ["-version"]);
  } catch (error) {
    throw new Error("cwebp is required; install libwebp or set CWEBP", {
      cause: error,
    });
  }
  await mkdir(destination, { recursive: true });
  const staging = await mkdtemp(resolve(destination, ".textures-"));
  const prepared = [];
  try {
    for (const [quality, divisor] of [
      ["high", highDivisor],
      ["standard", highDivisor * 2],
    ]) {
      const copy = structuredClone(manifest);
      const textures = [];
      for (const [index, source] of sources.entries()) {
        const original = await readFile(source);
        const originalInfo = pngInfo(original);
        const size = scaledSize(originalInfo, divisor);
        const file = `${quality}/texture_${index}.webp`;
        const png = resolve(staging, `${quality}/texture_${index}.png`);
        await mkdir(dirname(png), { recursive: true });
        if (divisor === 1) await writeFile(png, original);
        else
          await run("/usr/bin/sips", [
            "-z",
            String(size.height),
            String(size.width),
            source,
            "--out",
            png,
          ]);
        const resized = pngInfo(await readFile(png));
        await run(encoder, [
          "-lossless",
          "-exact",
          "-metadata",
          "icc",
          "-quiet",
          png,
          "-o",
          resolve(staging, file),
        ]);
        const actual = webpInfo(await readFile(resolve(staging, file)));
        if (actual.width !== size.width || actual.height !== size.height)
          throw new Error(
            "WebP texture dimensions do not match the requested size",
          );
        if (resized.icc && (!actual.icc || !resized.icc.equals(actual.icc)))
          throw new Error("WebP encoding did not preserve the PNG ICC profile");
        textures.push({ file, ...size });
      }
      copy.FileReferences.Textures = textures.map((t) => t.file);
      copy.KohakuTerrarium = { adapter: profile };
      copy.Demo = {
        profile,
        quality,
        textures,
        textureBytes: textures.reduce((n, t) => n + t.width * t.height * 4, 0),
      };
      await writeFile(
        resolve(staging, `${quality}.model3.json`),
        JSON.stringify(copy, null, 2),
      );
      prepared.push({ quality, copy, textures });
    }
    for (const { quality, copy, textures } of prepared) {
      await mkdir(resolve(destination, quality), { recursive: true });
      for (const { file } of textures)
        await rename(resolve(staging, file), resolve(destination, file));
      await rename(
        resolve(staging, `${quality}.model3.json`),
        resolve(destination, `${quality}.model3.json`),
      );
      for (const { file } of textures)
        await rm(resolve(destination, file.replace(/\.webp$/, ".png")), {
          force: true,
        });
      console.info(
        `${profile} ${quality}: ${copy.Demo.textureBytes / 1048576} MiB texture pixels`,
      );
    }
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
}
