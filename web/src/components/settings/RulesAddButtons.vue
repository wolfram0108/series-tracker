<script setup lang="ts">
import Button from "primevue/button"
import { t } from "../../i18n"

// The two buttons that add a rule or a table to a constructor: they stand in the head of the section of the rules, so the
// rules themselves take the room under it. `disabled` while the rules are read: a rule added then would be written over.
defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ rule: []; table: [] }>()
</script>

<template>
  <span class="rules-add">
    <Button :label="t('rules.rule.add')" icon="pi pi-plus-circle" size="small" :disabled="disabled" @click.stop="emit('rule')" />
    <Button :label="t('rules.table.add')" icon="pi pi-table" size="small" severity="secondary" :disabled="disabled"
            @click.stop="emit('table')" />
  </span>
</template>

<style scoped>
/* Their height is the one of the buttons of a head (styles/primitives.css). */
.rules-add { display: inline-flex; flex-wrap: wrap; gap: 0.375rem; flex: none; }
</style>
