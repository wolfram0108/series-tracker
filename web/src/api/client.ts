import createClient from "openapi-fetch"
import type { paths } from "./schema"

// The contract of the gateway names the body of a request only where the endpoint declares it (a model of the request). Many
// endpoints read their body themselves, as a JSON object, and answer a missing field with their own 400: the contract then says
// `requestBody?: never`, and a client typed by it would refuse every body. Such an operation takes a plain JSON value here —
// the one place that says so — and where the contract declares a body, the body is the declared one and is checked by the build.
type JsonObject = { content: { "application/json": Record<string, unknown> | unknown[] } }
type Writing = "post" | "put" | "patch" | "delete"
type WithBody<Operation, Method> = Method extends Writing
  ? Operation extends { requestBody?: never } ? Omit<Operation, "requestBody"> & { requestBody?: JsonObject } : Operation
  : Operation
type Contract = { [Path in keyof paths]: { [Method in keyof paths[Path]]: WithBody<paths[Path][Method], Method> } }

/** Типизированный HTTP-клиент на сгенерированных из OpenAPI типах.
 *  baseUrl пустой (относительные пути): в dev Vite проксирует /api → :5000,
 *  в проде фронт (/v2) и API (/api) на одном origin. Все маршруты
 *  доступны строго по контракту schema.d.ts. */
export const api = createClient<Contract>({ baseUrl: "" })
