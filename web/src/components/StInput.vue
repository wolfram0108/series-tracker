<script setup lang="ts">
import { ref, computed, useId } from "vue"

// Floating-label поле (constructor-item item-floating-label). Иконка —
// отдельным StIcon перед ним (как в оригинале). Опц. пароль-глазик.
const props = withDefaults(defineProps<{
  modelValue?: string
  label: string
  type?: "text" | "password"
}>(), { modelValue: "", type: "text" })

const emit = defineEmits<{ "update:modelValue": [string] }>()
const id = useId()
const show = ref(false)
const eType = computed(() =>
  props.type === "password" && !show.value ? "password" : "text")

function onInput(e: Event) {
  emit("update:modelValue", (e.target as HTMLInputElement).value)
}
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
      :title="show ? 'Скрыть' : 'Показать'"
      @click="show = !show"
    >
      <i class="pi" :class="show ? 'pi-eye-slash' : 'pi-eye'" />
    </button>
  </div>
</template>
