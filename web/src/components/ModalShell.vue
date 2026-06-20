<script setup lang="ts">
import { onMounted, onUnmounted } from "vue"

// Переиспользуемая оболочка модального окна (порт modern-modal): оверлей +
// шапка (заголовок + слот для вкладок/доп. + крестик) + тело + футер.
// База для всех модалок (подтверждение, настройки, add, статус, логи).
// fixedHeight: окно занимает фикс. высоту (настройки/логи — чтобы габарит
// не прыгал). Без него окно растёт под контент (как окно добавления в
// легаси: max-height 90vh, тело скроллится).
withDefaults(defineProps<{ title?: string; size?: "" | "xl" | "full"; fixedHeight?: boolean }>(), {
  title: "",
  size: "",
  fixedHeight: false,
})
const emit = defineEmits<{ (e: "close"): void }>()

// Закрытие по клику вне окна — только если ЖЕСТ начался на самом оверлее.
// Иначе выделение текста в поле с отпусканием за пределами окна порождает
// click по общему предку (оверлею в Chromium) и теряет введённые данные.
// Поэтому закрываем лишь когда и mousedown, и click пришлись на оверлей.
let pressedOnOverlay = false
function onOverlayMousedown(e: MouseEvent) {
  pressedOnOverlay = e.target === e.currentTarget
}
function onOverlayClick(e: MouseEvent) {
  if (e.target === e.currentTarget && pressedOnOverlay) emit("close")
  pressedOnOverlay = false
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close")
}
onMounted(() => document.addEventListener("keydown", onKey))
onUnmounted(() => document.removeEventListener("keydown", onKey))
</script>

<template>
  <div class="modal-overlay" @mousedown="onOverlayMousedown" @click="onOverlayClick">
    <div
      class="modern-modal"
      :class="[size === 'xl' ? 'modal-xl' : size === 'full' ? 'modal-full' : '', { 'modal-fixed': fixedHeight }]"
    >
      <div class="modern-header">
        <h5 class="modal-title"><slot name="title">{{ title }}</slot></h5>
        <slot name="header-extra" />
        <button class="modern-close" title="Закрыть" @click="emit('close')"><i class="pi pi-times"></i></button>
      </div>
      <div class="modern-body"><slot /></div>
      <div v-if="$slots.footer" class="modern-footer"><slot name="footer" /></div>
    </div>
  </div>
</template>
