<script setup lang="ts">
import { computed } from "vue"
import { useDropList } from "../composables/useDropList"

// A chevron button inside the group of a field: it opens a list of ready values to put into the field. It keeps no
// choice itself: a click on a value emits it. `load` runs before every opening, so the list is as the core has it now.
// The list is teleported into <body> (useDropList): no frame of an ancestor cuts it; it is worked from the keyboard as every list
// that drops from a trigger. One list for every field of this kind (the saved paths of a series, the sources and the queries of a
// series in the test of a profile).
export interface PickItem { value: string; label: string }

const props = withDefaults(defineProps<{
  items: PickItem[]
  title: string
  empty: string
  load?: () => Promise<void>
  minWidth?: number
}>(), { load: undefined, minWidth: 320 })
const emit = defineEmits<{ (e: "select", value: string): void }>()

const drop = useDropList({
  count: () => props.items.length,
  selected: () => -1,
  choose: (index) => emit("select", props.items[index].value),
  place: () => ({ align: "right", minWidth: props.minWidth }),
  before: async () => props.load?.(),
})
const { open, root, list, dropStyle, active, listId, optionId } = drop
const hasItems = computed(() => props.items.length > 0)
</script>

<template>
  <div
    ref="root" class="constructor-item pick-trigger" :class="{ open }" role="combobox" :title="title" :aria-label="title"
    v-bind="drop.triggerAttrs()" @click.stop="drop.toggle" @keydown="drop.onKey"
  >
    <i class="pi pi-chevron-down chevron" aria-hidden="true" />
    <Teleport to="body">
      <div v-if="open" :id="listId" ref="list" class="options-list pick-list" role="listbox" :aria-label="title" :style="dropStyle ?? undefined" @mousedown.prevent>
        <div v-if="!hasItems" class="pick-empty">{{ empty }}</div>
        <div
          v-for="(item, index) in items"
          :id="optionId(index)"
          :key="item.value"
          role="option"
          class="option"
          :class="{ active: index === active }"
          :aria-selected="false"
          @click.stop="drop.take(index)"
        >
          {{ item.label }}
        </div>
      </div>
    </Teleport>
  </div>
</template>
