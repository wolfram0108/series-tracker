import { definePreset } from "@primevue/themes"
import Aura from "@primevue/themes/aura"

// Производный пресет PrimeVue под токены series-tracker (frontend-rewrite §7, Ф2).
// Первая итерация для парити-галереи: основной цвет = Bootstrap-синий
// (#0d6efd, как --color-blue) и более крупные радиусы (~9px) под текущий
// вид. Точные значения подбираются по галерее и согласованию.
export const STPreset = definePreset(Aura, {
  semantic: {
    // Шкала primary — палитра Bootstrap blue (под --color-blue).
    primary: {
      50: "#e7f1ff",
      100: "#cfe2ff",
      200: "#9ec5fe",
      300: "#6ea8fe",
      400: "#3d8bfd",
      500: "#0d6efd",
      600: "#0b5ed7",
      700: "#0a58ca",
      800: "#084298",
      900: "#052c65",
      950: "#031633",
    },
    // Радиусы под текущий дизайн (--border-radius: 9px).
    borderRadius: {
      none: "0",
      xs: "2px",
      sm: "4px",
      md: "9px",
      lg: "12px",
      xl: "16px",
    },
  },
})
