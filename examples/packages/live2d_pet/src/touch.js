import { modelAdapter } from "../ui/models/index.js";

function inTriangle(x, y, vertices, a, b, c) {
  const side = (i, j) =>
    (x - vertices[j * 2]) * (vertices[i * 2 + 1] - vertices[j * 2 + 1]) -
    (vertices[i * 2] - vertices[j * 2]) * (y - vertices[j * 2 + 1]);
  const s1 = side(a, b),
    s2 = side(b, c),
    s3 = side(c, a);
  if (
    Math.abs(
      (vertices[b * 2] - vertices[a * 2]) *
        (vertices[c * 2 + 1] - vertices[a * 2 + 1]) -
        (vertices[c * 2] - vertices[a * 2]) *
          (vertices[b * 2 + 1] - vertices[a * 2 + 1]),
    ) < 1e-10
  )
    return false;
  return !((s1 < 0 || s2 < 0 || s3 < 0) && (s1 > 0 || s2 > 0 || s3 > 0));
}

export function hitRegion(player, clientX, clientY) {
  const rect = player.canvas.getBoundingClientRect();
  const b = player.bounds,
    aspect = player.canvas.width / player.canvas.height;
  const scale =
    Math.min(1.8 / (b.top - b.bottom), (1.8 * aspect) / (b.right - b.left)) *
    player.zoom;
  const x =
    ((((clientX - rect.left) / rect.width) * 2 - 1 - player.pan.x) * aspect) /
      scale +
    (b.left + b.right) / 2;
  const y =
    (1 - ((clientY - rect.top) / rect.height) * 2 - player.pan.y) / scale +
    (b.top + b.bottom) / 2;
  const model = player.model.getModel(),
    core = model.getModel();
  const heads = new Set(modelAdapter(player.model.profile).headParts);
  let body = false;
  for (let i = 0; i < model.getDrawableCount(); i++) {
    if (model.getDrawableOpacity(i) < 0.05) continue;
    const vertices = model.getDrawableVertices(i),
      indices = model.getDrawableVertexIndices(i);
    let hit = false;
    for (let j = 0; j < indices.length && !hit; j += 3)
      hit = inTriangle(
        x,
        y,
        vertices,
        indices[j],
        indices[j + 1],
        indices[j + 2],
      );
    if (!hit) continue;
    let part = core.drawables.parentPartIndices[i];
    while (part >= 0) {
      if (heads.has(core.parts.ids[part])) return "head";
      part = core.parts.parentIndices[part];
    }
    body = true;
  }
  return body ? "body" : null;
}

export function applyReaction(
  model,
  reaction,
  protectedParameters = new Set(),
  aliases = {},
) {
  const ids = model.getModel().parameters.ids;
  for (const [name, amount] of Object.entries(reaction || {})) {
    const target = aliases[name] || name;
    const index = ids.indexOf(target);
    if (
      index < 0 ||
      protectedParameters.has(name) ||
      protectedParameters.has(target)
    )
      continue;
    model.setParameterValueByIndex(
      index,
      Math.max(
        model.getParameterMinimumValue(index),
        Math.min(
          model.getParameterMaximumValue(index),
          model.getParameterValueByIndex(index) + amount,
        ),
      ),
    );
  }
}
