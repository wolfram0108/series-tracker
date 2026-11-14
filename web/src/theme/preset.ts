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
  components: {
    // The words of a toggle that is not chosen: a shade darker than Aura's slate, to keep 4.5:1 on its ground (#f1f5f9).
    togglebutton: { colorScheme: { light: { root: { color: "#617187" }, icon: { color: "#617187" } } } },
    // The words of a notice, a shade darker than Aura's 600: on their tint they were 2.8–4.4:1, now 4.7 and more.
    toast: {
      colorScheme: {
        light: {
          info: { color: "{blue.700}" },
          success: { color: "{green.700}" },
          warn: { color: "{yellow.700}" },
          error: { color: "{red.700}" },
        },
      },
    },
  },
})
