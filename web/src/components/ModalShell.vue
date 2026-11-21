<script setup lang="ts">
import { onMounted, onUnmounted, onUpdated, ref } from "vue"
import { useBodyScrollLock } from "../composables/useBodyScrollLock"
import { useConfirm } from "../composables/useConfirm"
import { provideWindowBusy } from "../composables/useWindowBusy"
import { hasFocusable, useDialog } from "../composables/useLayers"
import { useWindowBox } from "../composables/useWindowCover"
import { t } from "../i18n"

// Переиспользуемая оболочка модального окна (порт modern-modal): оверлей +
// шапка (заголовок + слот для вкладок/доп. + крестик) + тело + футер.
// База для всех модалок (подтверждение, настройки, add, статус, логи).
// fixedHeight: окно занимает фикс. высоту (настройки/логи — чтобы габарит
// не прыгал). Без него окно растёт под контент (как окно добавления в
// легаси: max-height 90vh, тело скроллится).
// dirty: the window holds what the user typed and has not saved; a way out the window takes itself (Escape, the cross, a click
// on the ground) asks first. A button of the window that says "Cancel" is the user's word and asks nothing.
const props = withDefaults(defineProps<{ title?: string; size?: "" | "xl" | "full"; fixedHeight?: boolean; dirty?: boolean }>(), {
  title: "",
  size: "",
  fixedHeight: false,
  dirty: false,
})
const emit = defineEmits<{ (e: "close"): void }>()
const confirm = useConfirm()
const room = ref<HTMLElement | null>(null)
// The tabs tell what they are reading (useWindowBusy); the bar under the title runs while any of them does.
const reading = provideWindowBusy()

async function requestClose(): Promise<void> {
  if (props.dirty) {
    const answer = await confirm.open({ title: t("window.discard.title"), message: t("window.discard.message") })
    if (!answer.confirmed) return
  }
  emit("close")
}

// Закрытие по клику вне окна — только если ЖЕСТ начался на самом оверлее.
// Иначе выделение текста в поле с отпусканием за пределами окна порождает
// click по общему предку (оверлею в Chromium) и теряет введённые данные.
// Поэтому закрываем лишь когда и mousedown, и click пришлись на оверлей.
let pressedOnOverlay = false
function onOverlayMousedown(e: MouseEvent) {
  pressedOnOverlay = e.target === e.currentTarget
}
function onOverlayClick(e: MouseEvent) {
  if (e.target === e.currentTarget && pressedOnOverlay) void requestClose()
  pressedOnOverlay = false
}

// The window is a layer (useLayers): Escape and Tab belong to the top one, the focus goes in and comes back to what opened it.
const { titleId } = useDialog(room, requestClose)
// The labels of the main screen under the window do not blur their backdrop (composables/useWindowCover.ts).
useWindowBox(room)

// фон не прокручивается, пока окно открыто (scroll chaining)
useBodyScrollLock()

// A screen-width breakpoint broke with the seventh tab, so the tabs fit the room they get:
// tighter padding, then icons, then — where even icons leave the title less than its one line — a line of their own under it.
// A word of the title is never broken for the tabs.
const header = ref<HTMLElement | null>(null)
let observer: ResizeObserver | null = null

// A body that scrolls and holds nothing the focus can reach is reached itself: a keyboard user scrolls it with the arrows.
const body = ref<HTMLElement | null>(null)
function reachBody(): void {
  const el = body.value
  if (!el) return
  if (el.scrollHeight > el.clientHeight + 1 && !hasFocusable(el)) el.tabIndex = 0
  else el.removeAttribute("tabindex")
}
// Asked once a frame: a body whose cards come and go as it scrolls changes many times between two frames.
let reachAsked = false
function reachSoon(): void {
  if (reachAsked) return
  reachAsked = true
  requestAnimationFrame(() => {
    reachAsked = false
    reachBody()
  })
}
let bodyResize: ResizeObserver | null = null
let bodyChange: MutationObserver | null = null
let shownTab: Element | null = null

// The title kept on one line, for the moment of asking, does not fit the room the head leaves it. Put back before anything is painted.
function titleCramped(title: HTMLElement | null): boolean {
  if (!title) return false
  title.style.whiteSpace = "nowrap"
  const cramped = title.scrollWidth > title.clientWidth + 1
  title.style.whiteSpace = ""
  return cramped
}

function fitTabs(follow = false) {
  const bar = header.value
  const strip = bar?.querySelector<HTMLElement>(".st-tabs")
  if (!bar || !strip) return
  const title = bar.querySelector<HTMLElement>(".modal-title")
  const active = strip.querySelector<HTMLElement>(".st-tab.active")
  const overflows = () => strip.scrollWidth > strip.clientWidth + 1
  const squeeze = () => {
    if (overflows()) bar.classList.add("tabs-tight")
    if (overflows()) bar.classList.add("tabs-compact")
    // The chosen tab does not fit even alone: its label goes too.
    if (active && active.offsetWidth > strip.clientWidth) bar.classList.add("tabs-icons")
  }
  // Without the transition the labels have their final width at the moment of measuring.
  bar.classList.add("tabs-measuring")
  bar.classList.remove("tabs-tight", "tabs-compact", "tabs-icons", "tabs-below")
  squeeze()
  if (titleCramped(title)) {
    bar.classList.remove("tabs-tight", "tabs-compact", "tabs-icons")
    bar.classList.add("tabs-below")
    squeeze()
  }
  // A resize brings the chosen tab back into sight; a re-render does it only when another tab was chosen.
  if (active && (follow || active !== shownTab)) active.scrollIntoView({ block: "nearest", inline: "nearest" })
  shownTab = active
  requestAnimationFrame(() => bar.classList.remove("tabs-measuring"))
}
onMounted(() => {
  observer = new ResizeObserver(() => fitTabs(true))
  if (header.value) observer.observe(header.value)
  bodyResize = new ResizeObserver(reachSoon)
  bodyChange = new MutationObserver(reachSoon)
  if (body.value) {
    bodyResize.observe(body.value)
    bodyChange.observe(body.value, { childList: true, subtree: true })
  }
  reachBody()
  void document.fonts?.ready.then(() => fitTabs(true))
  fitTabs(true)
})
onUpdated(() => fitTabs())
onUnmounted(() => {
  observer?.disconnect()
  bodyResize?.disconnect()
  bodyChange?.disconnect()
})
</script>

<template>
  <div class="modal-overlay" @mousedown="onOverlayMousedown" @click="onOverlayClick">
    <div
      ref="room"
      class="modern-modal" role="dialog" aria-modal="true" :aria-labelledby="titleId" tabindex="-1"
      :class="[size === 'xl' ? 'modal-xl' : size === 'full' ? 'modal-full' : '', { 'modal-fixed': fixedHeight }]"
    >
      <div ref="header" class="modern-header">
        <h2 :id="titleId" class="modal-title"><slot name="title">{{ title }}</slot></h2>
        <slot name="header-extra" />
        <button type="button" class="modern-close" :title="t('common.close')" :aria-label="t('common.close')" @click="requestClose">
          <i class="pi pi-times" aria-hidden="true"></i>
        </button>
        <div v-if="reading" class="modal-progress" role="progressbar" aria-busy="true" :aria-label="t('common.fetching')"></div>
      </div>
      <div ref="body" class="modern-body"><slot /></div>
      <div v-if="$slots.footer" class="modern-footer"><slot name="footer" /></div>
    </div>
  </div>
</template>
