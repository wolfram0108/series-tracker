// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { defineComponent, h, nextTick, provide, ref, type Ref } from "vue"
import { mount, type VueWrapper } from "@vue/test-utils"
import draggable from "vuedraggable"
import { CHANGE_ANSWER, nextAnswer, type ChangeAnswer } from "../lib/changeAnswer"
import OrderList from "./OrderList.vue"

// A window that holds the order like the core does: the list gets it as a prop, and the answers of the core come from
// the window. The engine of dragging does not run in jsdom: a drag is the new order the engine hands to the list.
function windowWith(items: Ref<string[]>, answer: Ref<ChangeAnswer> | null = null, busy = ref(false)) {
  const sent: string[][] = []
  const Host = defineComponent(() => {
    if (answer) provide(CHANGE_ANSWER, answer)
    return () => h(OrderList<string>, {
      items: items.value, itemKey: (item: string) => item, busy: busy.value,
      onOrder: (order: string[]) => sent.push(order),
    }, { item: ({ item, step }: { item: string; step: (event: KeyboardEvent) => void }) =>
      h("li", { class: "item", tabindex: 0, onKeydown: step }, item) })
  })
  return { w: mount(Host), sent }
}
const shown = (w: VueWrapper): string[] => w.findAll(".item").map((each) => each.text())
const drag = async (w: VueWrapper, order: string[]): Promise<void> => {
  w.findComponent(draggable).vm.$emit("update:modelValue", order)
  await nextTick()
}

describe("OrderList", () => {
  it("is built on the engine of the constructor of rules, and every item is one the engine may drag", () => {
    const { w } = windowWith(ref(["a", "b"]))
    expect(w.findComponent(draggable).exists()).toBe(true)
    expect(w.findAll(".item").map((each) => each.attributes("data-draggable"))).toEqual(["true", "true"])
  })

  it("sends the dragged order and shows it while the core has not answered", async () => {
    const items = ref(["a", "b", "c"])
    const { w, sent } = windowWith(items, ref({ n: 0, ok: true }))
    await drag(w, ["b", "a", "c"])
    expect(sent).toEqual([["b", "a", "c"]])
    expect(shown(w)).toEqual(["b", "a", "c"])
  })

  it("keeps the dragged order when the items come again in the old order before the core answers", async () => {
    const items = ref(["a", "b", "c"])
    const { w } = windowWith(items, ref({ n: 0, ok: true }))
    await drag(w, ["b", "a", "c"])
    items.value = ["a", "b", "c"]
    await nextTick()
    expect(shown(w)).toEqual(["b", "a", "c"])
  })

  it("goes back to the order the core holds when the core refuses", async () => {
    const items = ref(["a", "b", "c"])
    const answer = ref<ChangeAnswer>({ n: 0, ok: true })
    const { w } = windowWith(items, answer)
    await drag(w, ["b", "a", "c"])
    answer.value = nextAnswer(answer.value, false)
    await nextTick()
    expect(shown(w)).toEqual(["a", "b", "c"])
  })

  it("after an accepted order shows whatever order the core sends next, the core's own one too", async () => {
    const items = ref(["a", "b", "c"])
    const answer = ref<ChangeAnswer>({ n: 0, ok: true })
    const { w } = windowWith(items, answer)
    await drag(w, ["c", "a", "b"])
    answer.value = nextAnswer(answer.value, true)
    await nextTick()
    expect(shown(w)).toEqual(["c", "a", "b"])
    items.value = ["a", "c", "b"]
    await nextTick()
    expect(shown(w)).toEqual(["a", "c", "b"])
  })

  it("shows a new order of the core at once, even before the answer", async () => {
    const items = ref(["a", "b", "c"])
    const { w } = windowWith(items, ref({ n: 0, ok: true }))
    await drag(w, ["b", "a", "c"])
    items.value = ["b", "a", "c"]
    await nextTick()
    items.value = ["c", "b", "a"]
    await nextTick()
    expect(shown(w)).toEqual(["c", "b", "a"])
  })

  it("moves an item by one place with the arrow keys, in both directions, and not past the ends", async () => {
    const items = ref(["a", "b", "c"])
    const { w, sent } = windowWith(items)
    const [first, second] = w.findAll(".item")
    await first.trigger("keydown", { key: "ArrowUp" })
    await second.trigger("keydown", { key: "ArrowLeft" })
    expect(sent).toEqual([["b", "a", "c"]])
    items.value = ["b", "a", "c"]
    await nextTick()
    await w.findAll(".item")[1].trigger("keydown", { key: "ArrowDown" })
    await w.findAll(".item")[2].trigger("keydown", { key: "Enter" })
    expect(sent).toEqual([["b", "a", "c"], ["b", "c", "a"]])
  })

  it("takes no order while it is busy, and the engine is off", async () => {
    const items = ref(["a", "b"])
    const { w, sent } = windowWith(items, null, ref(true))
    expect(w.findComponent(draggable).vm.$attrs.disabled).toBe(true)
    await drag(w, ["b", "a"])
    await w.findAll(".item")[1].trigger("keydown", { key: "ArrowUp" })
    expect(sent).toEqual([])
    expect(shown(w)).toEqual(["a", "b"])
  })
})
