/** Resolve only after the browser can display the sprite. */
export async function decodeImage(source) {
  const image = new Image();
  image.src = source;
  await image.decode();
  return image;
}

export const DEFAULT_HIT_BOUNDS = Object.freeze({
  x: 0.2,
  y: 0.2,
  width: 0.6,
  height: 0.6,
});

/** Normalized opaque bounds of the first idle pose. */
export function spriteHitBounds(image, atlas) {
  const { cellWidth: width, cellHeight: height } = atlas;
  if (!(image.naturalWidth >= width && image.naturalHeight >= height))
    return DEFAULT_HIT_BOUNDS;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return DEFAULT_HIT_BOUNDS;
    context.drawImage(image, 0, 0, width, height, 0, 0, width, height);
    const { data } = context.getImageData(0, 0, width, height);
    let left = width,
      top = height,
      right = -1,
      bottom = -1;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (data[(y * width + x) * 4 + 3] < 32) continue;
        left = Math.min(left, x);
        top = Math.min(top, y);
        right = Math.max(right, x);
        bottom = Math.max(bottom, y);
      }
    }
    return right < left
      ? DEFAULT_HIT_BOUNDS
      : {
          x: left / width,
          y: top / height,
          width: (right - left + 1) / width,
          height: (bottom - top + 1) / height,
        };
  } catch {
    return DEFAULT_HIT_BOUNDS;
  }
}
