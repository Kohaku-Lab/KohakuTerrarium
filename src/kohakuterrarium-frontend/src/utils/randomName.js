// wandb-style two-word names: <adjective>-<noun>. Used as the default
// name when the user doesn't supply one explicitly. The pools are
// intentionally compact so newcomers see the same vocabulary often
// enough that names start to feel like nicknames rather than UUIDs.

const ADJECTIVES = [
  "amber",
  "lucid",
  "swift",
  "wandering",
  "gentle",
  "quiet",
  "bright",
  "patient",
  "scarlet",
  "azure",
  "hidden",
  "vivid",
  "crisp",
  "humble",
  "earnest",
  "playful",
  "candid",
  "nimble",
  "warm",
  "cosmic",
  "feral",
  "glowing",
  "dapper",
  "fluent",
  "rolling",
  "fleeting",
  "mossy",
  "frosted",
  "stellar",
  "salt",
  "lone",
  "quick",
]

const NOUNS = [
  "kohaku",
  "bonsai",
  "lantern",
  "river",
  "drift",
  "ember",
  "harbor",
  "meadow",
  "thicket",
  "fjord",
  "comet",
  "lichen",
  "marble",
  "atrium",
  "feather",
  "compass",
  "anchor",
  "summit",
  "garden",
  "echo",
  "willow",
  "tide",
  "spire",
  "lattice",
  "porch",
  "cipher",
  "moss",
  "cabin",
  "orchard",
  "trellis",
  "harbor",
  "verse",
]

function pick(array) {
  return array[Math.floor(Math.random() * array.length)]
}

/** Two-word random name, e.g. ``"amber-cabin"``. */
export function randomName() {
  return `${pick(ADJECTIVES)}-${pick(NOUNS)}`
}

/** Build a random name with a kind-specific prefix to disambiguate
 *  channels from creatures in logs. */
export function randomNameFor(kind) {
  switch (kind) {
    case "channel":
      return `bus-${randomName()}`
    case "terrarium":
      return `land-${randomName()}`
    default:
      return randomName()
  }
}
