import assert from "node:assert/strict";
import { test } from "node:test";
import { pngSize, scaledSize } from "./prepare-deepseek.mjs";

test("preserves rectangular atlas proportions and rejects invalid dimensions", () => {
  assert.deepEqual(scaledSize({ width: 4096, height: 8192 }, 4), {
    width: 1024,
    height: 2048,
  });
  assert.deepEqual(scaledSize({ width: 8192, height: 8192 }, 2), {
    width: 4096,
    height: 4096,
  });
  assert.throws(() => scaledSize({ width: 4095, height: 8192 }, 4));
  assert.throws(() => scaledSize({ width: 4096, height: 8192 }, 0));
  assert.throws(() => pngSize(Buffer.from("not a PNG")));
});
