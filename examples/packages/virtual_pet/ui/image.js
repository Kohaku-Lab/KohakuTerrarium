/** Resolve only after the browser can display the sprite. */
export async function decodeImage(source) {
  const image = new Image();
  image.src = source;
  await image.decode();
}
