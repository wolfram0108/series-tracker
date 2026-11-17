<script setup lang="ts">
import { computed, inject } from "vue"
import { useDropList } from "../composables/useDropList"
import { FIELD_LABEL } from "../lib/fieldLabel"
import { t } from "../i18n"
import NumberMark from "./NumberMark.vue"

// The select of a group (constructor-item item-select): frameless, it shows the value chosen and a chevron. Its list is teleported
// to <body> with position:fixed (useDropList): no frame of an ancestor (a window, an accordion) cuts it; its height is its content's,
// up to the edge of the window, upwards where there is no room below. It is a combobox of the keyboard (useDropList) and is named
// by the label of its group (lib/fieldLabel.ts) unless `ariaLabel` names it.
// `mark`: the number of an option in a numbered list (the actions of «ТО»), shown before its label as NumberMark.
interface Opt { label: string; value: unknown; mark?: number }
const props = withDefaults(defineProps<{
  modelValue?: unknown
  options: Opt[]
  placeholder?: string
  ariaLabel?: string
  /** A short text the closed select shows instead of the label of its value (a number of an action); the list shows labels. */
  display?: string
  /** The list is at least this wide (rem), wider than a narrow trigger (a number): its labels are read whole. */
  listMinWidth?: number
}>(), { placeholder: t("common.choose"), ariaLabel: undefined, display: undefined, listMinWidth: 0 })

const emit = defineEmits<{ "update:modelValue": [unknown] }>()
const fieldLabel = inject(FIELD_LABEL, null)
const selText = computed(() => {
  if (props.display !== undefined) return props.display
  const o = props.options.find((o) => o.value === props.modelValue)
  return o ? o.label : props.placeholder
})
const name = computed(() => props.ariaLabel ?? (fieldLabel?.() || props.placeholder))

const drop = useDropList({
  count: () => props.options.length,
  selected: () => props.options.findIndex((o) => o.value === props.modelValue),
  choose: (index) => emit("update:modelValue", props.options[index].value),
  place: () => ({ minWidth: props.listMinWidth * parseFloat(getComputedStyle(document.documentElement).fontSize) }),
})
const { open, root, list, dropStyle, active, listId, optionId } = drop
</script>

<template>
  <div
    ref="root" class="constructor-item item-select" :class="{ open }" role="combobox" :aria-label="name" v-bind="drop.triggerAttrs()"
    @click.stop="drop.toggle" @keydown="drop.onKey"
  >
    <span class="selected-value">{{ selText }}</span>
    <i class="pi pi-chevron-down chevron" aria-hidden="true" />
    <Teleport to="body">
      <!-- A press on an option must not move the focus off the trigger: the list would close before the click. -->
      <div v-if="open" :id="listId" ref="list" class="options-list" role="listbox" :aria-label="name" :style="dropStyle ?? undefined" @mousedown.prevent>
        <div
          v-for="(o, index) in options"
          :id="optionId(index)"
          :key="String(o.value)"
          role="option"
          class="option"
          :class="{ selected: o.value === modelValue, active: index === active }"
          :aria-selected="o.value === modelValue"
          @click.stop="drop.take(index)"
        ><NumberMark v-if="o.mark" :n="o.mark" />{{ o.label }}</div>
      </div>
    </Teleport>
  </div>
</template>
