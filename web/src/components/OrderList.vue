<script setup lang="ts" generic="T">
import { computed, h, inject, shallowRef, useAttrs, useSlots, watch } from "vue"
import draggable from "vuedraggable"
import { CHANGE_ANSWER } from "../lib/changeAnswer"

// A list in the order the user gives it, on the engine of the constructor of rules (vuedraggable): the item follows the
// pointer, the others make room, a shadow marks the place; the arrow keys on the item's handle move it by one place.
// The order is the core's: the list sends the new one and shows it until the core answers. An accepted order stays
// until the core's order comes back, a refused one goes back to the order the core holds; nothing jumps back meanwhile.
const props = withDefaults(defineProps<{
  items: T[]
  itemKey: (item: T) => string
  busy?: boolean
  tag?: string
  /** The part of an item it is dragged by; none: the whole item. */
  handle?: string
}>(), { busy: false, tag: "ol", handle: undefined })

const emit = defineEmits<{ (e: "order", items: T[]): void }>()

// The slot gets the item, its place, the arrow keys of its handle and the whole list as it is shown.
defineSlots<{
  item(props: { item: T; index: number; step: (event: KeyboardEvent) => void; items: T[] }): unknown
}>()

interface Pending { order: T[]; before: string; accepted: boolean }
const pending = shallowRef<Pending | null>(null)
const keysOf = (list: readonly T[]): string => JSON.stringify(list.map(props.itemKey))
const shown = computed<T[]>(() => pending.value?.order ?? props.items)

function send(order: T[]): void {
  if (props.busy || keysOf(order) === keysOf(shown.value)) return
  pending.value = { order, before: keysOf(props.items), accepted: false }
  emit("order", order)
}

function moveBy(index: number, by: number): void {
  const target = index + by
  if (target < 0 || target >= shown.value.length) return
  const order = [...shown.value]
  order.splice(target, 0, order.splice(index, 1)[0])
  send(order)
}

const BACK = ["ArrowUp", "ArrowLeft"]
const ON = ["ArrowDown", "ArrowRight"]
const stepOf = (index: number) => (event: KeyboardEvent): void => {
  const by = BACK.includes(event.key) ? -1 : ON.includes(event.key) ? 1 : 0
  if (!by) return
  event.preventDefault()
  moveBy(index, by)
}

// Another order of the core ends the wait; the same order made again (another field of the items changed) does not,
// unless the core has already accepted the order sent.
watch(() => props.items, (items) => {
  const wait = pending.value
  if (wait && (wait.accepted || keysOf(items) !== wait.before)) pending.value = null
})

// The engine marks the node its item slot returns as an item it may drag, so the slot gives it the element of the item
// itself (a nested <slot> would hand it a fragment, and nothing in the list could be dragged).
defineOptions({ inheritAttrs: false })
const attrs = useAttrs()
const slots = useSlots()
const List = () => h(draggable, {
  ...attrs, modelValue: shown.value, itemKey: props.itemKey, tag: props.tag, handle: props.handle, disabled: props.busy,
  animation: 200, ghostClass: "drag-ghost", "onUpdate:modelValue": send,
}, {
  item: ({ element, index }: { element: T; index: number }) =>
    slots.item?.({ item: element, index, step: stepOf(index), items: shown.value }) ?? [],
})

const answer = inject(CHANGE_ANSWER, null)
if (answer) {
  watch(answer, (next) => {
    const wait = pending.value
    if (!wait) return
    pending.value = next.ok ? { ...wait, accepted: true } : null
  })
}
</script>

<template>
  <List />
</template>
