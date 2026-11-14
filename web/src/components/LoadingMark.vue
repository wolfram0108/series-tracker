<script setup lang="ts">
import { t } from "../i18n"

// The mark of a read that is under way: a turning ring that tells itself to the reader of the screen. The place round it is the owner's.
</script>

<template>
  <i class="pi pi-spin pi-spinner loading-mark" role="status" :aria-label="t('common.fetching')" />
</template>
