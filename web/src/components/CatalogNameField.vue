<script setup lang="ts">
import { t } from "../i18n"
import SheetField from "./SheetField.vue"
import StValue from "./StValue.vue"

// The name of the catalog folder the chosen TMDB series makes (`Name (year) [tmdbid-N]`), read-only, with its copy button.
defineProps<{ name: string }>()
const emit = defineEmits<{ copy: [] }>()
</script>

<template>
  <SheetField class="is-readonly" :label="t('seriesForm.catalogName')" icon="pi pi-tag">
    <StValue :value="name" mono />
    <div class="constructor-item item-button-group">
      <button type="button" class="btn-icon" :title="t('common.copy')" :aria-label="t('common.copy')" @click="emit('copy')">
        <i class="pi pi-clipboard" />
      </button>
    </div>
  </SheetField>
</template>
