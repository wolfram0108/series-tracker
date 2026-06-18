import { reactive } from "vue"

export interface ConfirmCheckbox {
  text: string
  checked: boolean
}
export interface ConfirmResult {
  confirmed: boolean
  checkboxChecked: boolean
}
interface ConfirmState {
  visible: boolean
  title: string
  message: string
  checkbox: ConfirmCheckbox | null
  resolve: ((r: ConfirmResult) => void) | null
}

// Singleton-состояние: одно окно подтверждения на приложение, Promise-API.
const state = reactive<ConfirmState>({
  visible: false,
  title: "Подтверждение",
  message: "",
  checkbox: null,
  resolve: null,
})

/** Promise-based подтверждение (порт confirmationModal). open() возвращает
 *  { confirmed, checkboxChecked }; опциональный чекбокс — например
 *  «удалить из qBittorrent». */
export function useConfirm() {
  function open(opts: { title?: string; message: string; checkbox?: ConfirmCheckbox }): Promise<ConfirmResult> {
    state.title = opts.title ?? "Подтверждение"
    state.message = opts.message
    state.checkbox = opts.checkbox ? { ...opts.checkbox } : null
    state.visible = true
    return new Promise<ConfirmResult>((res) => {
      state.resolve = res
    })
  }
  function confirm() {
    state.resolve?.({ confirmed: true, checkboxChecked: state.checkbox?.checked ?? false })
    state.visible = false
  }
  function cancel() {
    state.resolve?.({ confirmed: false, checkboxChecked: false })
    state.visible = false
  }
  return { state, open, confirm, cancel }
}
