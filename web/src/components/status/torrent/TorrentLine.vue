<script setup lang="ts">
import { computed } from "vue"

// The line of an item of the editor as the rules of its tab read it (`D:… | name | K:… | X:…`, `T:… | A:… | L:… | N:… | C:…`):
// the raw facts of the file, field by field, nothing told about where the item went. Every field is text the user selects and
// copies as it is (`N:MifSnaiper`) into a rule. `skip`: the codes a heading above already shows (the folder of a group).
const props = defineProps<{ line: string; skip?: string[] }>()
const SEP = " | "
// A click with nothing dragged takes the field whole (code and all); a drag keeps what it took, a part of a field too.
function takeWhole(event: MouseEvent): void {
  const picked = getSelection()
  if (!picked || !picked.isCollapsed) return
  picked.selectAllChildren(event.currentTarget as Node)
}
const parts = computed(() => props.line.split(SEP).map((part) => {
  const found = /^([A-Z]{1,3}):(.*)$/.exec(part)
  return found ? { code: found[1], value: found[2], text: part } : { code: "", value: part, text: part }
}).filter((part) => !part.code || !(props.skip ?? []).includes(part.code)))
</script>

<template>
  <span class="torrent-line" :title="line">
    <template v-for="(part, index) in parts" :key="index">
      <span v-if="index" class="torrent-line-sep" aria-hidden="true"> | </span>
      <span class="torrent-line-field" :class="{ 'is-name': !part.code }" @click="takeWhole"><span v-if="part.code" class="torrent-line-code">{{ part.code }}:</span>{{ part.value }}</span>
    </template>
  </span>
</template>
