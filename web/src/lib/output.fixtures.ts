// The levers as the core describes them, for the tests of the windows that draw them.
import type { LeverSpec } from "./output"

/** What the core allows in each state (core/contracts/availability.py), as the layout carries it. */
export const AVAILABILITY: Record<string, string[]> = {
  sync: ["idle"], cancel_download: ["downloading", "pending", "processing"],
  choose_reference: ["referable"], prefer_variant: ["playable", "referable"],
}

export const SPECS: LeverSpec[] = [
  { name: "reference.order", target: "none", shape: "variant_order", default: [], consumer: "scan", queue: "reference" },
  { name: "reference.allow", target: "variant", shape: "flag", default: true, consumer: "scan", queue: "reference" },
  { name: "voice.take", target: "group", shape: "flag", default: true, consumer: "voices", queue: "voice" },
  { name: "voice.order", target: "group", shape: "variant_order", default: [], consumer: "voices", queue: "voice" },
  { name: "voice.allow", target: "variant", shape: "flag", default: true, consumer: "voices", queue: "voice" },
  { name: "markup.order", target: "none", shape: "variant_order", default: [], consumer: "planner", queue: "markup" },
  { name: "markup.allow", target: "variant", shape: "flag", default: true, consumer: "planner", queue: "markup" },
]
