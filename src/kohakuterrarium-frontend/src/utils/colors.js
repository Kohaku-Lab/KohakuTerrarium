/**
 * Gemstone color constants for programmatic use.
 * UnoCSS theme handles CSS classes; this is for JS-driven coloring (Vue Flow nodes, etc.)
 */

export const GEM = {
  sapphire: { light: "#D6E3F8", main: "#0F52BA", shadow: "#082567" },
  aquamarine: { light: "#D4EDE8", main: "#4C9989", shadow: "#1B6B5A" },
  taaffeite: { light: "#E8D5ED", main: "#A57EAE", shadow: "#6B4670" },
  iolite: { light: "#DDD0F0", main: "#5A4FCF", shadow: "#312A7A" },
  amber: { light: "#F5E6C8", main: "#D4920A", shadow: "#8B5E00" },
  coral: { light: "#F5D5D5", main: "#D46B6B", shadow: "#8B3A3A" },
  sage: { light: "#D5E8DA", main: "#5A9E6F", shadow: "#3A6B48" },
}

/** Map creature status to gem color */
export function statusColor(status) {
  switch (status) {
    case "running":
    case "processing":
      return GEM.aquamarine
    case "idle":
    case "done":
      return GEM.amber
    case "error":
      return GEM.coral
    default:
      return GEM.amber
  }
}

/** Map channel type to gem color */
export function channelColor(type) {
  switch (type) {
    case "queue":
      return GEM.aquamarine
    case "broadcast":
      return GEM.taaffeite
    default:
      return GEM.aquamarine
  }
}
