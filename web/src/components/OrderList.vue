<script setup lang="ts" generic="T">
import { h, useAttrs, useSlots } from "vue"
import draggable from "vuedraggable"
import { useAwaited } from "../composables/useAwaited"

// A list in the order the user gives it, on the engine of the constructor of rules (vuedraggable): the item follows the
// pointer, the others make room, a shadow marks the place; the arrow keys on the item's handle move it by one place.
// The order is the core's: the list sends the new one and shows it until the core answers (useAwaited).
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

const keysOf = (list: readonly T[]): string => JSON.stringify(list.map(props.itemKey))
const { shown, hold } = useAwaited(() => props.items, keysOf)

function send(order: T[]): void {
  if (props.busy || keysOf(order) === keysOf(shown.value)) return
  hold(order)
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
// Only on the handle itself: a field inside an item keeps its arrow keys.
const stepOf = (index: number) => (event: KeyboardEvent): void => {
  const by = event.target !== event.currentTarget ? 0 : BACK.includes(event.key) ? -1 : ON.includes(event.key) ? 1 : 0
  if (!by) return
  event.preventDefault()
  moveBy(index, by)
}

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
</script>

<template>
  <List />
</template>
