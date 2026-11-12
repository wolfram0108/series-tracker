<script setup lang="ts">
import { ref, computed, watch, useId } from "vue"
import { FS_RULES, type FsKind } from "../lib/fsnames"
import { t } from "../i18n"

// Floating-label поле (constructor-item item-floating-label). Иконка —
// отдельным StIcon перед ним (как в оригинале). Опц. пароль-глазик.
const props = withDefaults(defineProps<{
  modelValue?: string
  label: string
  type?: "text" | "password"
  kind?: FsKind
}>(), { modelValue: "", type: "text" })

const emit = defineEmits<{ "update:modelValue": [string] }>()
const id = useId()
const show = ref(false)
const eType = computed(() =>
  props.type === "password" && !show.value ? "password" : "text")
const rules = computed(() => (props.kind ? FS_RULES[props.kind] : null))

function onInput(e: Event) {
  const el = e.target as HTMLInputElement
  let value = el.value
  if (rules.value) {
    const live = rules.value.live(value)
    if (live !== value) {
      // Dropped characters sit before the caret (typed or pasted there),
      // so the caret moves back by the number removed.
      const caret = Math.max(0, (el.selectionStart ?? live.length) - (value.length - live.length))
      el.value = live
      el.setSelectionRange(caret, caret)
      value = live
    }
    value = rules.value.commit(value)
  }
  emit("update:modelValue", value)
}

// Values written by code (dropdown, TMDB, DB) obey the same rule as typing.
watch(() => props.modelValue, (v) => {
  if (!rules.value) return
  const clean = rules.value.commit(v)
  if (clean !== v) emit("update:modelValue", clean)
}, { immediate: true })
</script>

<template>
  <div class="constructor-item item-floating-label">
    <input
      :id="id"
      class="item-input"
      :type="eType"
      :value="modelValue"
      placeholder=" "
      @input="onInput"
    />
    <label :for="id">{{ label }}</label>
    <button
      v-if="type === 'password'"
      type="button"
      class="password-toggle-btn"
      :title="show ? t('common.hide') : t('common.show')"
      @click="show = !show"
    >
      <i class="pi" :class="show ? 'pi-eye-slash' : 'pi-eye'" />
    </button>
  </div>
</template>
