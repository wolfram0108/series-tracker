<script setup lang="ts">
import { t } from "../i18n"
import { isAbsolutePath } from "../lib/fsnames"
import SavedPathDropdown from "./SavedPathDropdown.vue"
import SheetField from "./SheetField.vue"
import SheetRow from "./SheetRow.vue"
import StInput from "./StInput.vue"
import StSwitch from "./StSwitch.vue"

// The rows «Информация» of the form of a series — in the window of a new series and in the properties of an existing one: the two
// names, the folder (a saved one may be chosen), the season, and the quality and the resolution that override the site's. The form is
// the screen's own state, handed whole and written in place; the states (valid, invalid, none) are the screen's own validation. The
// default slot holds what only one of the screens has (the profile of a torrent series), the slot `path-note` a note under the folder.
type VState = "valid" | "invalid" | null
export interface SeriesInfoForm {
  name: string
  name_en: string
  save_path: string
  season: string
  quality_override: string
  resolution_override: string
}
defineProps<{
  form: SeriesInfoForm
  states: { name: VState; name_en: VState; save_path: VState; season: VState }
  catalogName: string
  /** Whether the series may be taken as one with no seasons (the switch is shown). */
  canSwitchSeasons: boolean
}>()
// A series with no seasons: the field of the season is dimmed and its number is not sent.
const seasonless = defineModel<boolean>("seasonless", { required: true })
</script>

<template>
  <SheetRow :caption="t('seriesForm.infoTitle')">
    <div class="sheet-pair">
      <SheetField :label="t('seriesForm.nameRu')" icon="pi pi-pencil" :state="states.name">
        <StInput v-model="form.name" />
      </SheetField>
      <SheetField :label="t('seriesForm.nameEn')" icon="pi pi-language" :state="states.name_en">
        <StInput v-model="form.name_en" kind="filename" />
      </SheetField>
      <SheetField :label="t('seriesForm.savePath')" icon="pi pi-folder-open" :state="states.save_path">
        <StInput v-model="form.save_path" kind="path" />
        <SavedPathDropdown :catalog-name="catalogName" @select="form.save_path = $event" />
      </SheetField>
      <SheetField :label="t('seriesForm.season')" icon="pi pi-hashtag" :state="states.season" :class="{ 'is-disabled': seasonless }">
        <StInput v-model="form.season" :placeholder="t('seriesForm.seasonHint')" />
      </SheetField>
      <slot name="path-note" />
      <p v-if="form.save_path && !isAbsolutePath(form.save_path)" class="sheet-problem">{{ t("seriesForm.savePathAbsolute") }}</p>
      <SheetField v-if="canSwitchSeasons" :label="t('seriesForm.seasonless')" :title="t('seriesForm.seasonlessHint')" wide control>
        <StSwitch v-model="seasonless" />
      </SheetField>
      <SheetField :label="t('seriesForm.qualityOverride')" icon="pi pi-video">
        <StInput v-model="form.quality_override" kind="filename" :placeholder="t('seriesForm.manualHint')" />
      </SheetField>
      <SheetField :label="t('seriesForm.resolutionOverride')" icon="pi pi-desktop">
        <StInput v-model="form.resolution_override" kind="filename" :placeholder="t('seriesForm.manualHint')" />
      </SheetField>
      <slot />
    </div>
  </SheetRow>
</template>
