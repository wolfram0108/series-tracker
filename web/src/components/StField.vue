<script setup lang="ts">
import { ref, computed, useId } from "vue"

// Поле series-tracker (порт constructor-group): иконка-сегмент +
// floating-label + опц. пароль-глазик. Точный аналог оригинала.
const props = withDefaults(defineProps<{
  modelValue?: string
  label: string
  icon?: string
  type?: "text" | "password"
  state?: "valid" | "invalid" | null
}>(), { modelValue: "", type: "text", icon: "", state: null })

const emit = defineEmits<{ "update:modelValue": [string] }>()

const id = useId()
const show = ref(false)
const effectiveType = computed(() =>
  props.type === "password" && !show.value ? "password" : "text")
const groupClass = computed(() => ({
  "is-valid": props.state === "valid",
  "is-invalid": props.state === "invalid",
}))

function onInput(e: Event) {
  emit("update:modelValue", (e.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="input-constructor-group" :class="groupClass">
    <div v-if="icon" class="constructor-item item-label-icon"><i :class="icon" /></div>
    <div class="constructor-item item-floating-label">
      <input
        :id="id"
        class="item-input"
        :type="effectiveType"
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
  </div>
</template>
