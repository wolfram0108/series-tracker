import type { InjectionKey } from "vue"

/** The label of the field of a sheet a control stands in (components/SheetField.vue): the control names itself by it for the reader of
 *  the screen, so that its name is written once, as the label. */
export const FIELD_LABEL: InjectionKey<() => string> = Symbol("the label of a field of a sheet")
