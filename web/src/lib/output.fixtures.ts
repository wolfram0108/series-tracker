// The levers as the core describes them, for the tests of the windows that draw them.
import type { LeverSpec } from "./output"

export const SPECS: LeverSpec[] = [
  { name: "reference.choice", target: "none", shape: "reference_pick", default: null, consumer: "scan" },
  { name: "variant.allow", target: "variant", shape: "flag", default: true, consumer: "scan" },
  { name: "voice.take", target: "group", shape: "flag", default: true, consumer: "voices" },
  { name: "voice.player", target: "group", shape: "variant_pick", default: null, consumer: "voices" },
]
