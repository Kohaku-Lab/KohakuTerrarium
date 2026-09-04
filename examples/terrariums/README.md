# Example terrariums

Recipes that used to ship in `kt-biome` and were moved here in 3.0. They still
run; they were removed from the shipped catalog because each duplicates a
pattern a kept recipe already demonstrates, and four recipes teaching two
patterns dilutes the catalog.

| Recipe | Pattern | Shipped equivalent |
| --- | --- | --- |
| `pair_programming` | driver / navigator, asymmetric wiring | `@kt-biome/terrariums/swe_team` |
| `auto_research` | ideate → code → run → analyze pipeline | `@kt-biome/terrariums/deep_research` |

Run one with `kt run examples/terrariums/<name>/`.

Their role prompts predate the 3.0 prompt contract, so they still hand-write
channel topology that the framework now injects live from the running graph.
Treat them as working examples of recipe *structure*, not as models for prompt
authoring — see `@kt-biome/terrariums/adaptive_team` for the current shape.
