import { onMounted, onUnmounted } from "vue"

// Блокировка прокрутки фоновой страницы на время жизни модалки.
//
// Без неё колесо мыши над оверлеем/шапкой/футером (вне прокручиваемого
// тела) уходит на document и крутит фон за модалкой (scroll chaining).
// Инвариант «модалка открыта ⇒ фон не скроллится» закреплён здесь, в одной
// точке, и применяется в каждом корне оверлея (ModalShell, Login, Setup).
//
// Счётчик locks — на случай вложенных модалок: блокировку снимаем только
// когда закрылась последняя. savedOverflow/savedPaddingRight хранят
// исходные inline-стили body, чтобы корректно восстановить их при снятии.
let locks = 0
let savedOverflow = ""
let savedPaddingRight = ""

function lock(): void {
  if (locks === 0) {
    const body = document.body
    // ширина вертикального скроллбара: компенсируем padding-right, иначе при
    // overflow:hidden скроллбар пропадёт и фон «прыгнет» вправо
    const scrollbar = window.innerWidth - document.documentElement.clientWidth
    savedOverflow = body.style.overflow
    savedPaddingRight = body.style.paddingRight
    body.style.overflow = "hidden"
    if (scrollbar > 0) {
      const current = parseFloat(getComputedStyle(body).paddingRight) || 0
      body.style.paddingRight = `${current + scrollbar}px`
    }
  }
  locks++
}

function unlock(): void {
  locks = Math.max(0, locks - 1)
  if (locks === 0) {
    document.body.style.overflow = savedOverflow
    document.body.style.paddingRight = savedPaddingRight
  }
}

/** Замораживает прокрутку фона, пока смонтирован компонент-модалка; снимает
 *  заморозку при размонтировании (с учётом вложенных модалок). */
export function useBodyScrollLock(): void {
  onMounted(lock)
  onUnmounted(unlock)
}
