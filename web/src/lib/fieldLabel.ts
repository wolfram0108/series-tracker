import type { InjectionKey, Ref } from "vue"

/** The label of the field of a sheet a control stands in (components/SheetField.vue, or any group with a label segment — components/StGroup.vue):
 *  the control names itself by it for the reader of the screen, so that its name is written once, as the label. */
export const FIELD_LABEL: InjectionKey<() => string> = Symbol("the label of a field of a sheet")

/** Where the label segment (components/StLabel.vue) of a group tells its words to the group: the first label names the group. */
export const GROUP_LABEL: InjectionKey<Ref<string>> = Symbol("the label segment of a group")
