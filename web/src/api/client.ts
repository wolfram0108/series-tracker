import createClient from "openapi-fetch"
import type { paths } from "./schema"

/** Типизированный HTTP-клиент на сгенерированных из OpenAPI типах.
 *  baseUrl пустой (относительные пути): в dev Vite проксирует /api → :5000,
 *  в проде фронт (/v2) и API (/api) на одном origin. Все 62 маршрута
 *  доступны строго по контракту schema.d.ts. */
export const api = createClient<paths>({ baseUrl: "" })
