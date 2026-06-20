// Расчёт позиции/высоты выпадающего списка. Список рендерится через
// <Teleport to="body"> с position:fixed по координатам триггера — поэтому
// он НЕ зависит от overflow/clip любых предков (модалок, аккордеонов,
// fieldset'ов). Это фундамент: класс ошибки «выпадашку режет рамка
// контейнера» устранён в принципе, а не для конкретного места.
//
// Высота списка — по содержимому; потолок — свободное место от триггера до
// края окна браузера; снизу мало места → раскрываем вверх; список
// прижимается к краю окна по горизонтали, чтобы не уходить за вьюпорт.

export interface DropStyle {
  position: "fixed"
  left: string
  width: string
  top: string
  bottom: string
  maxHeight: string
}

/**
 * @param trigger  элемент-якорь (сам триггер списка)
 * @param gap      зазор между триггером и списком, px
 * @param margin   отступ от края окна, px
 * @param min      минимальная высота, ниже которой раскрываем в сторону с
 *                 бо́льшим запасом, px
 * @param align    к какому краю триггера прижать список ('left' | 'right')
 * @param minWidth минимальная ширина списка, px (0 — по ширине триггера)
 */
export function computeDropStyle(
  trigger: HTMLElement,
  {
    gap = 6,
    margin = 8,
    min = 140,
    align = "left",
    minWidth = 0,
  }: { gap?: number; margin?: number; min?: number; align?: "left" | "right"; minWidth?: number } = {},
): DropStyle {
  const rect = trigger.getBoundingClientRect()
  const width = Math.max(minWidth, rect.width)
  // прижать к нужному краю триггера, затем удержать в пределах окна
  let left = align === "right" ? rect.right - width : rect.left
  left = Math.min(left, window.innerWidth - margin - width)
  left = Math.max(margin, left)

  const below = window.innerHeight - rect.bottom - gap - margin
  const above = rect.top - gap - margin
  const base = {
    position: "fixed" as const,
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
  }
  // вниз, если места снизу достаточно или его не меньше, чем сверху
  if (below >= min || below >= above) {
    return {
      ...base,
      top: `${Math.round(rect.bottom + gap)}px`,
      bottom: "auto",
      maxHeight: `${Math.max(min, Math.floor(below))}px`,
    }
  }
  // иначе — вверх
  return {
    ...base,
    top: "auto",
    bottom: `${Math.round(window.innerHeight - rect.top + gap)}px`,
    maxHeight: `${Math.max(min, Math.floor(above))}px`,
  }
}
