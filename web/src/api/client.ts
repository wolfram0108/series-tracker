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

/** The typed HTTP client on the types generated from the OpenAPI document.
 *  An empty baseUrl (relative paths): in dev Vite proxies /api to :5000, in production the front and the API are on
 *  one origin. Every route is reached only by the contract of schema.d.ts. */
export const api = createClient<Contract>({ baseUrl: "" })
