<script setup lang="ts">
import { t } from "../i18n"
import SheetField from "./SheetField.vue"
import StInput from "./StInput.vue"

// The search of a series in TMDB, in the window of a new series and in the properties of one: a field of a sheet with its button.
const query = defineModel<string>({ required: true })
defineProps<{ loading: boolean }>()
const emit = defineEmits<{ search: [] }>()
</script>

<template>
  <SheetField :label="t('seriesForm.tmdbQuery')" icon="pi pi-search">
    <StInput v-model="query" :placeholder="t('seriesForm.tmdbPlaceholder')" @keyup.enter="emit('search')" />
    <div class="constructor-item item-button-group">
      <button type="button" class="btn-icon btn-add btn-text-icon" :disabled="loading" @click="emit('search')">
        <i class="pi" :class="loading ? 'pi-spin pi-spinner' : 'pi-search'" /><span>{{ t("common.find") }}</span>
      </button>
    </div>
  </SheetField>
</template>
