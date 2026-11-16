import { reactive } from "vue"
import { t } from "../i18n"

export interface ConfirmResult {
  confirmed: boolean
}
interface ConfirmState {
  visible: boolean
  title: string
  message: string
  resolve: ((r: ConfirmResult) => void) | null
}

// One confirmation window for the whole application, asked through a promise.
const state = reactive<ConfirmState>({
  visible: false,
  title: t("common.confirmTitle"),
  message: "",
  resolve: null,
})

/** A confirmation asked through a promise: open() comes back with { confirmed }. */
export function useConfirm() {
  function open(opts: { title?: string; message: string }): Promise<ConfirmResult> {
    // A question asked over an unanswered one cancels it: its asker is told "no", not left waiting for ever.
    state.resolve?.({ confirmed: false })
    state.title = opts.title ?? t("common.confirmTitle")
    state.message = opts.message
    state.visible = true
    return new Promise<ConfirmResult>((res) => {
      state.resolve = res
    })
  }
  function confirm() {
    state.resolve?.({ confirmed: true })
    state.visible = false
  }
  function cancel() {
    state.resolve?.({ confirmed: false })
    state.visible = false
  }
  return { state, open, confirm, cancel }
}
