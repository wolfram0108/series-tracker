import type { InjectionKey, Ref } from "vue"

// The answer of the core to the last change a window sent (n counts the answers). A list that shows the order the user
// dragged before the core holds it learns from it whether to wait for that order or to go back to the one it holds.
export interface ChangeAnswer { n: number; ok: boolean }

export const CHANGE_ANSWER: InjectionKey<Readonly<Ref<ChangeAnswer>>> = Symbol("change-answer")

export const nextAnswer = (answer: ChangeAnswer, ok: boolean): ChangeAnswer => ({ n: answer.n + 1, ok })
