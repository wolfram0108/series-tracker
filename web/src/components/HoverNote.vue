<script setup lang="ts">
import { ref } from "vue"
import Popover from "primevue/popover"

// A piece of text with more behind it: the more (the slot `more`) shows in a card by hover or focus, and by a press where there is
// no hover; it goes with the pointer, the focus, a press elsewhere or the press that begins a drag. The text is dotted under, the
// one hint of the window that there is more. Without `more` it is plain text: nothing to focus, nothing to open.
const props = defineProps<{ more: boolean }>()
const card = ref<InstanceType<typeof Popover> | null>(null)

function show(event: Event): void {
  if (props.more) card.value?.show(event)
}
function hide(): void {
  card.value?.hide()
}
</script>

<template>
  <span v-if="!more"><slot /></span>
  <span
    v-else class="has-more" tabindex="0" @mouseenter="show" @mouseleave="hide" @focus="show" @blur="hide"
    @pointerdown="hide" @click="show"
  >
    <slot />
    <Popover ref="card" class="hover-note"><slot name="more" /></Popover>
  </span>
</template>
