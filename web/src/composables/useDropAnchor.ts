// Расчёт высоты/направления выпадающего списка по свободному месту до края
// экрана (как в оригинальном ConstructorItemSelect). Высота списка — по
// содержимому; потолок — доступное место от триггера до границы окна
// браузера; при нехватке места снизу список раскрывается вверх. Скролл
// включается только когда содержимое не влезает в это место.

export interface DropStyle {
  top: string
  bottom: string
  maxHeight: string
}

/**
 * @param trigger элемент-якорь (сам триггер списка)
 * @param gap     зазор между триггером и списком, px
 * @param margin  отступ от края окна, px
 * @param min     минимальная высота, ниже которой раскрываем в сторону с
 *                бо́льшим запасом, px
 */
export function computeDropStyle(
  trigger: HTMLElement,
  { gap = 6, margin = 8, min = 140 }: { gap?: number; margin?: number; min?: number } = {},
): DropStyle {
  const rect = trigger.getBoundingClientRect()
  const below = window.innerHeight - rect.bottom - gap - margin
  const above = rect.top - gap - margin
  // вниз, если места снизу достаточно или его не меньше, чем сверху
  if (below >= min || below >= above) {
    return { top: `calc(100% + ${gap}px)`, bottom: "auto", maxHeight: `${Math.max(min, Math.floor(below))}px` }
  }
  // иначе — вверх
  return { top: "auto", bottom: `calc(100% + ${gap}px)`, maxHeight: `${Math.max(min, Math.floor(above))}px` }
}
